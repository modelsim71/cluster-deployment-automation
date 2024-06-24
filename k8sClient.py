import kubernetes
import yaml
import time
import host
import sys
from typing import Optional
from typing import Callable
from logger import logger

oc_url = "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/"


class K8sClient:
    def __init__(self, kubeconfig: str, host: host.Host = host.LocalHost()):
        self._kc = kubeconfig
        c = yaml.safe_load(host.read_file(kubeconfig))
        self._api_client = kubernetes.config.new_client_from_config_dict(c)
        self._client = kubernetes.client.CoreV1Api(self._api_client)
        self._host = host

    def is_ready(self, name: str) -> bool:
        for e in self._client.list_node().items:
            for con in e.status.conditions:
                if con.type == "Ready":
                    if name == e.metadata.name:
                        return str(con.status) == "True"
        return False

    def get_nodes(self) -> list[str]:
        return [e.metadata.name for e in self._client.list_node().items]

    def wait_ready(self, name: str, cb: Optional[Callable[[], None]] = None) -> None:
        logger.info(f"waiting for {name} to be ready")
        while True:
            if self.is_ready(name):
                break
            else:
                time.sleep(1)
            if cb:
                cb()
            self.approve_csr()

    def delete_node(self, node: str) -> None:
        logger.info(f"Deleting node {node}")
        self.oc(f"delete node {node}")

    def approve_csr(self) -> None:
        certs_api = kubernetes.client.CertificatesV1Api(self._api_client)
        for e in certs_api.list_certificate_signing_request().items:
            if e.status.conditions is None:
                self.oc(f"adm certificate approve {e.metadata.name}")

    def get_ip(self, name: str) -> Optional[str]:
        for e in self._client.list_node().items:
            if name == e.metadata.name:
                for addr in e.status.addresses:
                    if addr.type == "InternalIP":
                        return str(addr.address)
        return None

    def oc(self, cmd: str, must_succeed: bool = False) -> host.Result:
        cmd = f"oc {cmd} --kubeconfig {self._kc}"
        if must_succeed:
            return self._host.run_or_die(cmd)
        else:
            return self._host.run(cmd)

    def oc_run_or_die(self, cmd: str) -> host.Result:
        return self.oc(cmd, must_succeed=True)

    def wait_for_mcp(self, mcp_name: str, resource: str = "resource") -> None:
        time.sleep(60)
        iteration = 0
        max_tries = 10
        start = time.time()
        while True:
            ret = self.oc(f"wait mcp {mcp_name} --for condition=updated --timeout=20m")
            if ret.returncode == 0:
                break
            if iteration >= max_tries:
                logger.info(ret)
                logger.error(f"mcp {mcp_name} failed to update for {resource} after {max_tries}, quitting ...")
                sys.exit(-1)
            iteration = iteration + 1
            time.sleep(60)
        minutes, seconds = divmod(int(time.time() - start), 60)
        logger.info(f"It took {minutes}m {seconds}s for {resource} (attempts: {iteration})")

    def wait_for_crd(self, name: str, cr_name: str, namespace: str) -> None:
        logger.info(f"Waiting for crd {cr_name} to become available")
        ret = self.oc(f"get {cr_name}/{name} -n {namespace}")
        retries = 10
        while ret.returncode != 0:
            time.sleep(10)
            ret = self.oc(f"get {cr_name}/{name} -n {namespace}")
            retries -= 1
            if retries <= 0:
                logger.error_and_exit(f"Failed to get cr {cr_name}/{name}")
