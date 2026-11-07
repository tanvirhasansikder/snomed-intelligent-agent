"""
Live, read-only probe of single BioPortal requests (no retries, nothing stored).

    python -m evaluation.bioportal_probe 115668003 71388002

For each concept it requests the concept record, and /children page 1 with
page sizes 100 and 10, and prints the HTTP outcome and counts only. Used to
tell a lasting server fault on one concept from a temporary outage.
"""

import argparse
import time

from crawler.bioportal_client import BioPortalClient


def _try(label, call):
    started = time.time()
    try:
        data = call()
    except Exception as exc:  # noqa: BLE001 - report every failure kind
        print(f"   {label}: FAIL after {time.time() - started:.1f}s: {str(exc)[:110]}")
        return
    if isinstance(data, dict):
        detail = f"totalCount={data.get('totalCount')} items={len(data.get('collection') or [])}" \
            if "collection" in data else "record ok"
    else:
        detail = type(data).__name__
    print(f"   {label}: OK in {time.time() - started:.1f}s, {detail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("concepts", nargs="+")
    args = parser.parse_args()
    client = BioPortalClient(max_retries=0)
    for cid in args.concepts:
        print(cid)
        _try("concept record", lambda: client._request_json(client._class_url(cid)))
        for size in (100, 10):
            _try(f"children pagesize={size}", lambda: client._request_json(
                client._class_url(cid, "children"), params={"page": 1, "pagesize": size}))


if __name__ == "__main__":
    main()
