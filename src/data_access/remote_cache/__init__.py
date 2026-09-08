"""R2 remote-cache sync — dormant infrastructure only.

Nothing in this repo calls anything in this package yet. No page,
pipeline, or repository (including RadarSignalRepository) reads or
writes through it. It exists so a future GitHub Actions scanner can
upload the existing local data/cache/*.json files to a private
Cloudflare R2 bucket, and so the Streamlit app can later read the same
data — without changing any existing JSON shape, eligibility rule, or
UI behavior. See sync.py for the manifest-last upload/read functions and
r2_client.py for the (lazily-imported) real R2 client construction."""
