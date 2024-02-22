import time
import host
import sys
from host import Host
from nfs import NFS
from logger import logger
import paramiko
import os
import shutil
from typing import Optional
from jinja2 import Template
from clustersConfig import NodeConfig


# cleans microshift artifacts from previous installation
def cleanup_microshift(h: host.Host, version: str) -> None:
    files = ["rhocp.toml", "fast-datapath.toml", "kickstart.ks", "*.tar", "*.iso"]

    for file in files:
        if os.path.exists(f"./{file}"):
            os.remove(f"./{file}")

    cmd = f"podman rm -f minimal-microshift-server"
    output = h.run(cmd)
    logger.info(output.out)

    cleanup_compose_cli(h)
    cleanup_blueprints(h)
    cleanup_sources(h, version)

    cleanup_cache_path = "/var/cache/osbuild-worker/osbuild-store/stage/"

    if os.path.exists(cleanup_cache_path):
        shutil.rmtree(cleanup_cache_path)


# cleanup blueprints
def cleanup_blueprints(h: host.Host) -> None:
    composer_cli_cmd("blueprints delete microshift-installer", h)
    composer_cli_cmd("blueprints delete minimal-microshift", h)


def cleanup_sources(h: host.Host, version: str) -> None:
    composer_cli_cmd("sources delete fast-datapath", h)
    composer_cli_cmd(f"sources delete rhocp-{version}", h)


# cleans composes generated by composer cli
def cleanup_compose_cli(h: host.Host) -> None:
    old_compose = h.run("composer-cli compose status").out
    old_compose_splitlines = old_compose.splitlines()

    for line in old_compose_splitlines[1:]:
        compose_id = line.split(" ")[0]
        if 'FAILED' or 'FINISHED' not in line:
            cmd = f" composer-cli compose cancel {compose_id}"
            output = h.run(cmd)
            logger.info(output.out)
        cmd = f"composer-cli compose delete {compose_id}"
        output = h.run(cmd)
        logger.info(output.out)


# uses jinja to generate a kickstart file
def generate_kickstart(rhel_number: str, uname_m: str) -> None:
    with open('pull_secret.json', 'r') as f_in:
        file_contents = f_in.read()

    with open('/root/.ssh/id_rsa.pub', 'r') as ssh_in:
        ssh_contents = ssh_in.read()

    with open('kickstart.ks.j2', 'r') as f:
        lines = f.read()

    with open('kickstart.ks', 'w') as f_out:
        template = Template(lines)
        f_out.write(template.render(rhel_number=rhel_number, uname_m=uname_m, pull_secret=file_contents, ssh_key=ssh_contents))


# generates non static toml files
def generate_toml_file(content: str, file_name: str, h: host.Host) -> None:
    with open(file_name, 'w') as file:
        file.write(content)


# generates final iso
def generate_final_iso(file: str, name_of_final_iso: str, h: host.Host) -> None:
    cmd = f"sudo mkksiso kickstart.ks {file} {name_of_final_iso}"
    output = h.run(cmd)
    logger.info(output.out)
    logger.info("Microshift iso is ready in final.iso")


def change_permissions_to_read_for_all(file: str) -> None:
    uid = os.getuid()
    gid = os.getgid()

    os.chown(file, uid, gid)
    os.chmod(file, 444)


def vgrename(h: host.Host) -> None:
    vgname_command = "vgs --noheadings -o vg_name"
    vgname_output = h.run(vgname_command)
    vgname = vgname_output.out.split(" ")[0]

    cmd = f"sudo vgrename {vgname} rhel"
    output = h.run(cmd)
    logger.info(output.out)


# runs various composer-cli commands
def composer_cli_cmd(subcommand: str, h: host.Host, file_name: Optional[str] = None) -> str:
    if file_name is None:
        cmd = f"sudo composer-cli {subcommand}"
    else:
        cmd = f"sudo composer-cli {subcommand} {file_name}"
    output = h.run(cmd)
    logger.info(output.out)
    return output.out


def wait_for_build_to_finish(h: host.Host, build_id_two: Optional[str] = None) -> None:
    while True:
        status_output = composer_cli_cmd(f"compose status", h)

        if build_id_two:
            build_status_line = next((line for line in status_output.split('\n') if build_id_two in line), None)
            if build_status_line and 'FINISHED' in build_status_line:
                break
        else:
            if 'FINISHED' in status_output:
                break
        logger.info("waiting on build to finish")
        time.sleep(5)


# generates an iso file that builds microshift
def iso_builder(h: host.Host, name_of_final_iso: str, version: str) -> None:
    rhel_number = '9'
    cmd = "uname -m"
    uname_m = h.run(cmd).out.strip()

    cleanup_microshift(h, version)
    generate_kickstart(rhel_number, uname_m)

    rhel_version = 'rhel9'
    rhel_name = 'RHEL 9'

    content_rhocp = f'''
id = "rhocp-{version}"
name = "Red Hat OpenShift Container Platform {version} for {rhel_name}"
type = "yum-baseurl"
url = "https://cdn.redhat.com/content/dist/layered/{rhel_version}/{uname_m}/rhocp/{version}/os"
check_gpg = true
check_ssl = true
system = false
rhsm = true'''.strip()

    rhocp_file = "rhocp.toml"
    generate_toml_file(content_rhocp, rhocp_file, h)

    content_fdp = f'''
id = "fast-datapath"
name = "Fast Datapath for {rhel_name}"
type = "yum-baseurl"
url = "https://cdn.redhat.com/content/dist/layered/{rhel_version}/{uname_m}/fast-datapath/os"
check_gpg = true
check_ssl = true
system = false
rhsm = true'''.strip()

    fdp_file = "fast-datapath.toml"
    generate_toml_file(content_fdp, fdp_file, h)

    composer_cli_cmd("sources add", h, rhocp_file)
    composer_cli_cmd("sources add", h, fdp_file)
    composer_cli_cmd("blueprints push", h, "minimal-microshift.toml")

    build_id_result = composer_cli_cmd(f"compose start-ostree --ref 'rhel/9/{uname_m}/edge' minimal-microshift edge-container", h)
    build_id = build_id_result.split(" ")[1]
    logger.info(build_id)

    wait_for_build_to_finish(h)

    composer_cli_cmd(f"compose image {build_id}", h)
    change_permissions_to_read_for_all(f"{build_id}-container.tar")
    image_id_command = f"sudo podman load -i ./{build_id}-container.tar"

    result = h.run(image_id_command)
    image_id = result.out.split(':')[-1].strip()
    cmd = f"sudo podman run -d --name=minimal-microshift-server -p 8085:8080 {image_id}"
    result = h.run(cmd)

    composer_cli_cmd("blueprints push", h, "microshift-installer.toml")

    url = "http://127.0.0.1:8085/repo/"
    ref = f"rhel/{rhel_number}/{uname_m}/edge"
    build_command_two = composer_cli_cmd(f"compose start-ostree --url {url} --ref \"{ref}\" microshift-installer edge-installer", h)
    build_id_two = build_command_two.split(" ")[1]
    time.sleep(10)
    wait_for_build_to_finish(h, build_id_two)

    composer_cli_cmd(f"compose image {build_id_two}", h)
    change_permissions_to_read_for_all(f"{build_id_two}-installer.iso")

    vgrename(h)
    generate_final_iso(f"{build_id_two}-installer.iso", name_of_final_iso, h)
    shutil.rmtree("/var/cache/osbuild-worker/osbuild-store/stage/")


def deploy(cluster_name: str, node: NodeConfig, external_port: str, version: str) -> None:
    lh = host.LocalHost()
    bmc = host.bmc_from_host_name_or_ip(node.node, node.bmc_ip, node.bmc_user, node.bmc_password)
    h = Host(node.node, bmc)
    name_of_final_iso = os.path.join(os.getcwd(), 'final.iso')
    login_uname = "redhat"

    iso_builder(lh, name_of_final_iso, version)

    if os.path.exists(f"{name_of_final_iso}"):
        logger.info(f"The file {name_of_final_iso} exists.")
    else:
        logger.error(f"The file {name_of_final_iso} does not exist.")
        sys.exit(-1)
    logger.info("Microshift iso inserted")

    nfs = NFS(lh, external_port)
    iso = nfs.host_file(name_of_final_iso)
    h.boot_iso_redfish(iso)
    h.ssh_connect(login_uname)
    logger.info("Microshift finished booting")
