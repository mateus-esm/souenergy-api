"""Push updated files to VPS."""
import paramiko, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HOST, PORT, USER, PASSWORD = "72.61.219.156", 22, "root", "fg453@@#j45fx&M"
REMOTE = "/root/souenergy-api"
FILES  = ["scraper.py", "api.py", "requirements.txt"]

import os
BASE = os.path.dirname(os.path.abspath(__file__))

def run(ssh, cmd, timeout=120):
    ch = ssh.get_transport().open_session()
    ch.settimeout(timeout)
    ch.exec_command(cmd)
    out, err = b"", b""
    while True:
        if ch.recv_ready(): out += ch.recv(4096)
        if ch.recv_stderr_ready(): err += ch.recv_stderr(4096)
        if ch.exit_status_ready():
            while ch.recv_ready(): out += ch.recv(4096)
            while ch.recv_stderr_ready(): err += ch.recv_stderr(4096)
            break
        time.sleep(0.1)
    o = out.decode('utf-8', errors='replace').strip()
    e = err.decode('utf-8', errors='replace').strip()
    if o: print(o)
    if e: print("[ERR]", e)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, PORT, USER, PASSWORD)
print("Connected.")

sftp = ssh.open_sftp()
for f in FILES:
    print(f"Uploading {f}...")
    sftp.put(os.path.join(BASE, f), f"{REMOTE}/{f}")
sftp.close()

run(ssh, "systemctl restart souenergy")
time.sleep(3)
run(ssh, "systemctl status souenergy --no-pager -l")
run(ssh, "curl -s http://localhost:8000/")
ssh.close()
print("\nUpdate complete!")
