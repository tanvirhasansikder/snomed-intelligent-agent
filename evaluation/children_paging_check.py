"""
Live, read-only check of BioPortal's paging for /children (pilot review).

    python -m evaluation.children_paging_check --config configs/pilot.json

For every sampled concept, and the parent the sampling walk came from, it
requests the first two pages of /children directly (bypassing the cache) and
prints only the paging fields and the number of items on each page. It also
prints how the sampling walk ended. About 48 BioPortal requests; nothing is
stored and no API key is printed.
"""

import argparse
import json

from crawler.bioportal_client import BioPortalClient
from evaluation.runner import load_config, resolve

PAGING_KEYS = ("page", "pageCount", "totalCount", "prevPage", "nextPage")


def _page_summary(client, concept_id, page):
    url = client._class_url(concept_id, "children")
    data = client._request_json(url, params={"page": page, "pagesize": 100})
    if isinstance(data, list):
        return f"plain list of {len(data)}"
    if not isinstance(data, dict):
        return f"unexpected {type(data).__name__}"
    fields = {k: data.get(k) for k in PAGING_KEYS if k in data}
    links = data.get("links")
    if isinstance(links, dict):
        fields["links.nextPage"] = "set" if links.get("nextPage") else links.get("nextPage")
    items = data.get("collection")
    return f"{fields} items={len(items) if isinstance(items, list) else 'none'}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    dataset = json.loads(resolve(config, "dataset_path").read_text(encoding="utf-8"))
    client = BioPortalClient()
    for sample in dataset["samples"]:
        cid, path = sample["concept_id"], sample.get("descent_path") or []
        print(f"{cid}: target_depth={sample.get('target_depth')} descent_depth={sample.get('descent_depth')} "
              f"stopped_at_leaf={sample.get('stopped_at_leaf')}")
        for page in (1, 2):
            print(f"   concept page {page}: {_page_summary(client, cid, page)}")
        if len(path) >= 2:
            print(f"   parent {path[-2]} page 1: {_page_summary(client, path[-2], 1)}")


if __name__ == "__main__":
    main()
