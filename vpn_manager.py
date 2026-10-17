import os
import socket
import subprocess
import time

class OpenVPNManager:
    def __init__(self, socket_path="/run/openvpn.sock", host="127.0.0.1", port=7505):
        self.socket_path = socket_path
        self.host = host
        self.port = port

    def _connect(self, timeout=2.0):
        if os.path.exists(self.socket_path):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(self.socket_path)
            # Read initial greeting banner
            try:
                s.recv(1024)
            except Exception:
                pass
            return s
        else:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((self.host, self.port))
            try:
                s.recv(1024)
            except Exception:
                pass
            return s

    def send_command(self, cmd, timeout=3.0):
        s = None
        try:
            s = self._connect(timeout=timeout)
            s.sendall(f"{cmd}\n".encode("utf-8"))
            data = b""
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    if b"END\r\n" in data or b"END\n" in data or b"SUCCESS:" in data or b"ERROR:" in data:
                        break
                except socket.timeout:
                    break
            return data.decode("utf-8", errors="ignore")
        except Exception as e:
            return None
        finally:
            if s:
                try:
                    s.close()
                except Exception:
                    pass

    def is_active(self):
        # 1. First attempt: check via management socket
        out = self.send_command("pid", timeout=1.5)
        if out and "SUCCESS: pid=" in out:
            return True
            
        # 2. Second attempt: check supervisorctl
        try:
            res = subprocess.run(["supervisorctl", "status", "openvpn"], capture_output=True, text=True, timeout=3)
            if "RUNNING" in res.stdout:
                return True
        except Exception:
            pass

        # 3. Third attempt: check pgrep / pidfile
        try:
            res = subprocess.run(["pgrep", "-x", "openvpn"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                return True
        except Exception:
            pass

        return False

    def restart_service(self):
        # Restart via supervisorctl
        try:
            res = subprocess.run(["supervisorctl", "restart", "openvpn"], capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                return True
        except Exception as e:
            print(f"Error restarting via supervisorctl: {e}")

        # Fallback: kill -HUP or restart process
        try:
            out = self.send_command("signal SIGTERM")
            time.sleep(1)
            return True
        except Exception:
            return False

    def reload_crl(self):
        # SIGHUP signals OpenVPN to reload configuration and revoked certificates (crl.pem)
        out = self.send_command("signal SIGHUP")
        if out and "SUCCESS" in out:
            return True
        return False

    def kill_client(self, client_name):
        out = self.send_command(f"kill {client_name}")
        if out and ("SUCCESS" in out or "ERROR" not in out):
            return True
        return False

    def get_online_clients_from_mgmt(self):
        clients = set()
        out = self.send_command("status 2", timeout=2.0)
        if not out:
            return None
            
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("CLIENT_LIST,"):
                parts = line.split(",")
                if len(parts) > 1:
                    cn = parts[1].strip()
                    if cn and cn != "Common Name" and cn != "UNDEF":
                        clients.add(cn.lower())
        return clients

vpn_manager = OpenVPNManager()
