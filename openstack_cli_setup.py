#!/usr/bin/env python3
import os
import subprocess
import sys
import yaml

from pathlib import Path
import platform
import shutil

VENV_DIR = Path(".venv")
REQUIREMENTS = Path("requirements.txt")
KUBECONFIG_FILE = Path(os.getenv('KUBECONFIG'))

def run(cmd, **kwargs):
    print(f"🟢 Running: {' '.join(cmd)}")
    res = subprocess.run(cmd, check=True, **kwargs)
    return res

def create_virtualenv():
    if not VENV_DIR.exists():
        print("📦 Creating virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
    else:
        print("✅ Virtualenv already exists.")

def install_dependencies():

    dependencies = ['python-openstackclient',
                    'python-masakariclient',
                    'python-cinderclient',
                    'python-heatclient',
                    'python-glanceclient',
                    'python-neutronclient',
                    'python-octaviaclient']
    text = '\n'.join(dependencies)
    REQUIREMENTS.write_text(text)
    run([str(VENV_DIR / "bin" / "pip"), "install", "-r", str(REQUIREMENTS)])

def init_openstack_client(kubectl_bin):
    data, path = get_clouds_yaml_from_client(kubectl_bin)
    clouds_yaml = Path(path)
    if not clouds_yaml.exists():
        print(f"❌ clouds.yaml not found at {clouds_yaml}")
        sys.exit(1)
    print(f"✅ Found clouds.yaml: {clouds_yaml}")
    print(f"✅ Check main work")

    run([str(VENV_DIR / "bin" / "openstack"), "endpoint", "list"])

def create_activation_script(clouds_path):
    script_path = Path("activate_openstack.sh")
    script_content = f"""#!/bin/bash
source "{VENV_DIR}/bin/activate"
export OS_CLIENT_CONFIG_FILE="{clouds_path}"
export KUBECONFIG="{KUBECONFIG_FILE.resolve()}"
echo "✅ OpenStack environment activated."
echo "KUBECONFIG set to {KUBECONFIG_FILE.resolve()}"
echo "Now You can use 'openstack' CLI and kubectl."
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)
    print(f"✅ Created activation script: {script_path}")

def install_kubectl():
    """Intall kubectl if not installed"""
    if os.getenv('KUBECTL_PATH'):
        return str(os.getenv('KUBECTL_PATH'))
    # For seed node we have constant path
    seed_kubectl_path = '/home/ubuntu/bootstrap/dev/bin/kubectl'
    if os.path.exists(seed_kubectl_path):
        return seed_kubectl_path
    kubectl_path = Path.home() / ".local/bin/kubectl"
    kubectl_path.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("kubectl"):
        print(f"✅ kubectl already installed: {shutil.which('kubectl')}")
        return shutil.which("kubectl")

    # Check platform
    system = platform.system().lower()
    arch = platform.machine()
    if arch in ("x86_64", "amd64"):
        arch = "amd64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = "amd64"

    # get stable kubectl
    try:
        import urllib.request
        stable_url = "https://dl.k8s.io/release/stable.txt"
        with urllib.request.urlopen(stable_url) as response:
            version = response.read().decode().strip()
    except Exception:
        version = "v1.27.0"  # fallback
        print(f"⚠️ Could not get stable version, using fallback {version}")

    download_url = f"https://dl.k8s.io/release/{version}/bin/{system}/{arch}/kubectl"
    #download_url = 'https://artifactory.mcp.mirantis.net/artifactory/binary-dev-kaas-local/openstack/bin/utils/kubectl'
    print(f"📥 Downloading kubectl from {download_url} ...")
    urllib.request.urlretrieve(download_url, kubectl_path)
    kubectl_path.chmod(0o755)
    subprocess.run(["chmod", "+x", kubectl_path], check=True)
    print(f"✅ kubectl installed at {kubectl_path}")
    return str(kubectl_path)

def get_ingress_ip(kubectl_bin):
    if not KUBECONFIG_FILE.exists():
        print(f"❌ KUBECONFIG file {KUBECONFIG_FILE} does not exist")
        return
    env = os.environ.copy()
    env["KUBECONFIG"] = str(KUBECONFIG_FILE.resolve())
    res = run([kubectl_bin,
              "get", "service", "-n", "openstack", "ingress", "-o",
              "jsonpath={.status.loadBalancer.ingress[0].ip}"], env=env, text=True, capture_output=True)
    ingress_ip = res.stdout.strip()
    return ingress_ip

def get_clouds_yaml_from_client(kubectl_bin):
    if not KUBECONFIG_FILE.exists():
        print(f"❌ KUBECONFIG file {KUBECONFIG_FILE} does not exist")
        return
    env = os.environ.copy()
    env["KUBECONFIG"] = str(KUBECONFIG_FILE.resolve())
    res = run([kubectl_bin,
              "exec", "-n", "openstack", "deploy/keystone-client", "--", "cat", "/etc/openstack/clouds.yaml"], env=env, text=True, capture_output=True)
    data = yaml.safe_load(res.stdout.strip())
    data['clouds']['admin']['auth']['auth_url'] = 'https://keystone.it.just.works'
    data['clouds']['admin']['verify'] = False
    data['clouds']['admin']['interface'] = 'public'
    data['clouds']['admin']['endpoint_type'] = 'publicURL'
    data_full = {'clouds': {'admin': data.get('clouds').get('admin')}}

    # Save file
    output_path = Path.cwd() / 'clouds.yaml'
    with open(output_path, 'w') as clouds_yaml:
        clouds_yaml.write(yaml.dump(data_full))
    return yaml.dump(data.get('clouds').get('admin')), str(output_path)

def modify_hosts_file(kubectl_bin):
    domain = '.it.just.works'
    endpoints = ['horizon', 'masakari', 'keystone', 'placement', 'barbican', 'cloudformation', 'cinder', 'gnocchi', 'glance',
                 'neutron', 'aodh', 'heat', 'openstack-store', 'designate', 'nova', 'octavia', 'glance-api']
    ip = get_ingress_ip(kubectl_bin=kubectl_bin)


    # Sudo required. Copy original hosts to /etc/hosts.bak
    shutil.copy("/etc/hosts", "/etc/hosts.bak")
    print(f"✅ Original /etc/hosts filed saved as /etc/hosts.bak")

    # Create new hosts with all data from original except .it.just.works domains
    with open("/etc/hosts", 'r') as orig_hosts:
        with open('/etc/new_hosts', 'w') as new_hosts:
            for line in orig_hosts.readlines():
                if domain not in line:
                    new_hosts.write(line)

    # Add all endpoints to hosts file
    with open('/etc/new_hosts', 'a+') as new_hosts:
        endpoint_full = [endpoint + domain for endpoint in endpoints]
        host_resolve_ip_list = [f"{ip} {endpoint}\n" for endpoint in endpoint_full]
        for entry in host_resolve_ip_list:
            new_hosts.write(entry)

    # Cp new file to /etc/hosts
    shutil.copy("/etc/new_hosts", "/etc/hosts")

def main():
    create_virtualenv()
    install_dependencies()
    kubectl_bin = install_kubectl()
    _, path = get_clouds_yaml_from_client(kubectl_bin)
    modify_hosts_file(kubectl_bin)
    init_openstack_client(kubectl_bin)
    create_activation_script(clouds_path=path)
    print("\n🎉 Setup complete!")
    print("To use OpenStack CLI run")
    print("   source activate_openstack.sh")

if __name__ == "__main__":
    main()
