"""Per-user login helper; starts the UI on Codex desktop launch, without API calls."""
import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
import time

from autostart import InstanceLock, LaunchPolicy, codex_desktop_running, user_data


def launch_monitor():
    probe=InstanceLock(user_data()/'ui')
    if not probe.acquire():
        return
    probe.close()
    subprocess.Popen([sys.executable,str(Path(__file__).with_name('app.py'))],
                     creationflags=subprocess.CREATE_NO_WINDOW,
                     cwd=Path(__file__).parent)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if args.check:
        print(json.dumps({'codex_running':codex_desktop_running(),
                          'enabled':(user_data()/'autostart.enabled').exists()}))
        return
    directory=user_data()
    directory.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=directory/'watcher.log',level=logging.WARNING,
                        format='%(asctime)s %(message)s',encoding='utf-8')
    lock=InstanceLock(directory/'watcher')
    if not lock.acquire():
        return
    policy=LaunchPolicy(launch_monitor)
    try:
        while (directory/'autostart.enabled').exists():
            try:
                policy.tick(codex_desktop_running())
            except Exception:
                logging.exception('Codex launch detection failed')
            time.sleep(3)
    finally:
        lock.close()


if __name__=='__main__':
    main()
