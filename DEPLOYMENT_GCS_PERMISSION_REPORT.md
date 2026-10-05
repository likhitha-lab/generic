# Deployment GCS Permission Report — Upload 500 (`storage.buckets.get` Forbidden)

## 1. Which service account is attached to the backend

The Cloud Run runtime service account is identified directly in the exception
itself:
```
resume-gcs-service@developer-project-likhitha.iam.gserviceaccount.com
```

**This does not match this repo's own provisioning docs.**
`scripts/setup-gcp-cicd.sh` creates and grants roles to a SA named
`resume-builder-runtime@developer-project-likhitha.iam.gserviceaccount.com`
(see `RUNTIME_SA_NAME="resume-builder-runtime"`), and `deploy.yml` deploys
with `--service-account "${{ secrets.GCP_RUNTIME_SERVICE_ACCOUNT }}"`. The
live error proves `GCP_RUNTIME_SERVICE_ACCOUNT` currently resolves to
`resume-gcs-service`, a **different** account than the one the setup script
provisions and grants IAM roles to — so it's unsurprising it has none of
those bindings. Either:
- `resume-gcs-service` was created and wired up separately (manually, or by
  an earlier/different setup pass) and never got the IAM grants
  `setup-gcp-cicd.sh` gives to `resume-builder-runtime`, or
- the two were always meant to be the same identity and the GitHub secret
  just points at the wrong one.

Recommend reconciling this (point `GCP_RUNTIME_SERVICE_ACCOUNT` at whichever
account is the intended long-term runtime identity, and update
`setup-gcp-cicd.sh`/`DEPLOYMENT_GCP.md` to match) — not done here, it's a
naming/ownership decision, not a bug to silently paper over. The commands
below target the account actually in use today, `resume-gcs-service`, so
upload works immediately regardless of that decision.

## 2. Root cause

`app/storage/gcs.py`'s `GCSStorageService.__init__` calls
`self._bucket.exists()`, which issues a `storage.buckets.get` API call — a
**bucket-level** permission. `resume-gcs-service` doesn't have it, so this
call raises `Forbidden`, which the (prior) code turned into a fatal
`StorageError`, 500-ing on the very first request that touches storage
(upload).

Critically, **this is not just a missing grant — it's a scope mismatch by
design**: `storage.buckets.get` is deliberately excluded from
`roles/storage.objectAdmin` (and `objectViewer`/`objectCreator`). Those
object-scoped roles let a service account fully read/write/delete/list every
*object* in a bucket without being able to read the *bucket's own metadata*
— that's intentional GCS IAM separation, not an oversight. So even the
correctly-scoped, least-privilege `roles/storage.objectAdmin` that this
project's own `setup-gcp-cicd.sh` grants would **never** satisfy this
specific check. The bug existed in the code's assumptions, not only in a
missing IAM binding.

## 3. Is bucket-level IAM expected here?

Yes, for exactly one permission: `storage.buckets.get`. Everything else the
app does (upload, download, delete, list, exists-check on an *object*) is
object-scoped and already covered by `roles/storage.objectAdmin`. Scope the
extra bucket-level grant to the **specific bucket** (`resume_bucket08`), not
project-wide — only one bucket is used, and project-wide bucket-metadata
read is broader than needed.

## 4. Should the app require `storage.buckets.get` during initialization?

No. Requiring it forces every deployment to grant a broader permission than
the app's actual data-path operations need, which fights the project's own
documented least-privilege intent (`setup-gcp-cicd.sh`'s own comment: "Do not
grant `roles/owner` or `roles/editor`... both scoped to exactly what each
identity needs"). It should be a nice-to-have startup sanity check, not a
hard requirement.

## 5. Should the existence check be skipped if object-level permissions are sufficient?

Yes — implemented (see §7). `Forbidden` on the bucket-level check is now a
**warning, not a fatal error**: object uploads/downloads (which only need
object-level permissions, already granted) keep working regardless. If the
bucket genuinely doesn't exist *and* the SA lacks bucket-get, that surfaces
on first real upload/download instead of at boot — `upload_file`/
`download_file` already have their own explicit `Forbidden`/`NotFound`
handling with clear `StorageError` messages, so the failure mode is still
clear, just later. The one path still fatal at startup: the check
*succeeding* and confirming the bucket doesn't exist (`exists()` returns
`False`, no exception) — that's an unambiguous misconfiguration, still worth
catching immediately.

## 6. Exact gcloud commands — minimum permissions needed

```bash
PROJECT_ID="developer-project-likhitha"
BUCKET="resume_bucket08"
SA="resume-gcs-service@developer-project-likhitha.iam.gserviceaccount.com"

# Object-level CRUD on this bucket's objects (upload/download/delete/list) -
# confirm this is actually already granted; the prior fatal check on
# bucket.exists() meant this was never actually exercised/verified live.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" \
  --role="roles/storage.objectAdmin"

# Bucket-level read (storage.buckets.get) - what bucket.exists() needs.
# roles/storage.legacyBucketReader is the smallest PREDEFINED role that
# includes it (also includes storage.objects.list, harmless overlap with
# objectAdmin above).
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" \
  --role="roles/storage.legacyBucketReader"
```

If you want the absolute minimum surface (exactly `storage.buckets.get`,
nothing else) instead of `legacyBucketReader`'s small overlap, use a custom
role:
```bash
gcloud iam roles create bucketGetOnly --project="${PROJECT_ID}" \
  --title="Bucket metadata read only" \
  --permissions="storage.buckets.get" \
  --stage=GA

gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA}" \
  --role="projects/${PROJECT_ID}/roles/bucketGetOnly"
```
Either is sufficient; `legacyBucketReader` needs no ongoing custom-role
maintenance and is the pragmatic default.

Verify afterward:
```bash
gcloud storage buckets get-iam-policy "gs://${BUCKET}" \
  --format="table(bindings.role, bindings.members)"
```

Note: with the code change in §7, the `legacyBucketReader` grant is now
*optional* (removes a harmless warning from the logs) — the `objectAdmin`
grant is the one that actually matters for upload/download to work at all.

## 7. Code changes made (minimal)

**`backend/app/storage/gcs.py`** — `Forbidden` on the constructor's
`bucket.exists()` check is now logged as a warning and construction
continues, instead of raising `StorageError`. The `NotFound`-equivalent path
(check succeeds, bucket confirmed absent) is unchanged — still fatal. No
change to `upload_file`/`download_file`/`delete_file`/`file_exists`/
`list_files` — their own `Forbidden`/`NotFound` handling was already correct
and is untouched.

**`backend/tests/test_storage_gcs.py`** — updated
`test_permission_denied_on_bucket_check_raises_storage_error` (now
`test_permission_denied_on_bucket_check_is_non_fatal`) to assert construction
succeeds under `Forbidden`, matching the new intended behavior, instead of
asserting it raises.

Verified: `pytest tests/` — **927 passed, 10 deselected**, unchanged count
from before this fix.

## Summary

| # | Question | Answer |
|---|---|---|
| 1 | Attached SA | `resume-gcs-service@developer-project-likhitha.iam.gserviceaccount.com` — does not match `resume-builder-runtime` in setup scripts/docs, reconcile separately |
| 2 | Required GCS roles | `roles/storage.objectAdmin` (object CRUD) + `storage.buckets.get` (bucket-level, via `legacyBucketReader` or a custom role) |
| 3 | Bucket-level IAM expected? | Yes, for `storage.buckets.get` only, scoped to the one bucket in use |
| 4 | Should app require `storage.buckets.get` at init? | No — conflicts with least-privilege object-only grants by GCS IAM design |
| 5 | Skip existence check if object-level perms suffice? | Yes — implemented, `Forbidden` is now a warning, not fatal |
| 6 | gcloud commands | See §6 |
| 7 | Code changes | `gcs.py` constructor + matching test update, both minimal, functionality preserved |
