"""Install the pinned public Argos EN→AR model at build time, never on requests."""
import argparse
import hashlib
import tempfile
import time
import zipfile
from pathlib import Path
import httpx

URL="https://argos-net.com/v1/translate-en_ar-1_0.argosmodel"
SIZE=88502631
SHA256="e2a6e84337f7ebbb55f9dc61dcba3a861c0d786ae5a46fa198fd8d6473f5c775"

def install(destination, archive=None):
    destination=Path(destination).resolve();destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clover-arabic-model-") as temp:
        path=Path(archive) if archive else Path(temp)/"en-ar.argosmodel"
        if not archive:
            path.touch()
            with httpx.Client(timeout=45,follow_redirects=True) as client:
                failures=0
                while path.stat().st_size<SIZE:
                    offset=path.stat().st_size
                    try:
                        response=client.get(URL,headers={"Range":f"bytes={offset}-{min(SIZE-1,offset+5*1024*1024-1)}"})
                        response.raise_for_status()
                        if response.status_code==200 and offset==0:
                            path.write_bytes(response.content)
                        elif response.status_code==206 and response.headers.get("content-range","").startswith(f"bytes {offset}-"):
                            with path.open("ab") as output: output.write(response.content)
                        else: raise RuntimeError("Unexpected model download response")
                        failures=0
                    except (httpx.HTTPError,RuntimeError):
                        failures+=1
                        if failures>=3: raise
                        time.sleep(1)
        if path.stat().st_size!=SIZE or hashlib.sha256(path.read_bytes()).hexdigest()!=SHA256:
            raise ValueError("Arabic model checksum verification failed")
        with zipfile.ZipFile(path) as package:
            if package.testzip(): raise ValueError("Arabic model archive is corrupt")
            for member in package.infolist():
                parts=Path(member.filename).parts
                if not parts or parts[0]!="en_ar": raise ValueError("Unexpected model package path")
                relative=Path(*parts[1:]); target=(destination/relative).resolve()
                if target!=destination and destination not in target.parents: raise ValueError("Unsafe model package path")
                if member.is_dir(): target.mkdir(parents=True,exist_ok=True)
                else:
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(package.read(member))
        print(f"Installed checksum-verified Arabic translation model: {destination}")

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--destination",required=True);parser.add_argument("--archive")
    args=parser.parse_args();install(args.destination,args.archive)
