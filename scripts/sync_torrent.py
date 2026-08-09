# Add secret in the keyring: server_ip and password
# import keyring
# keyring.set_password("samba", "torrent", "password")
# keyring.set_password("server", "ip", "x.x.x.x")

import logging
import ntpath
import os
import time
import keyring
import smbclient.shutil as smb_shutil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("torrent_sync")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

SOURCE_DIR = os.path.join(os.path.expanduser("~"), "Downloads", "Torrent")
SMB_SHARE = "torrent-dropbox$"
SMB_USERNAME = "torrent"
DELAY_BEFORE_PROCESSING_FILE = 5  # seconds

class TorrentHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory:
            self.process_file(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self.process_file(event.dest_path)

    def process_file(self, file_path):
        filename = os.path.basename(file_path)

        if not filename.lower().endswith(".torrent"):
            return

        time.sleep(DELAY_BEFORE_PROCESSING_FILE)

        if not os.path.exists(file_path):
            logger.warning(f"[!] {filename} no longer exists (probably renamed), ignored.")
            return

        logger.info(f"[*] New file detected: {filename}")

        try:
            server_ip = keyring.get_password("server", "ip")
            if server_ip is None:
                raise RuntimeError("Server IP address not found in the keyring.")

            dest_dir = r"\\{}\{}".format(server_ip, SMB_SHARE)
            dest_path = ntpath.join(dest_dir, filename)

            password = keyring.get_password("samba", SMB_USERNAME)
            if password is None:
                raise RuntimeError("SMB password not found in the keyring.")

            logger.info(f"[*] Copying to {dest_dir}...")
            smb_shutil.copy2(
                file_path,
                dest_path,
                username=SMB_USERNAME,
                password=password,
            )
            logger.info(f"[+] Success: {filename} copied.")

        except (FileNotFoundError, PermissionError, OSError) as e:
            logger.error(f"Copy failed for {filename}: {e}")
            return
        except RuntimeError as e:
            logger.error(f"Configuration error: {e}")
            return
        except Exception:
            logger.exception(f"Unexpected error while copying {filename}")
            return

        try:
            os.remove(file_path)
            logger.info(f"[*] Deleted local file: {filename}")
        except Exception as e:
            logger.warning(f"Failed to delete local file {filename}: {e}")


if __name__ == "__main__":
    if not os.path.isdir(SOURCE_DIR):
        raise SystemExit(f"[!] Source folder does not exist: {SOURCE_DIR}")

    logger.info(f"--- Starting monitoring on: {SOURCE_DIR} ---")

    event_handler = TorrentHandler()
    observer = Observer()
    observer.schedule(event_handler, SOURCE_DIR, recursive=False)

    try:
        observer.start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
