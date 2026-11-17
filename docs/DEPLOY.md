# Deploying RFM Studio on Google Cloud

Everything is done in the Google Cloud console — no command line needed. Plan for about 45 minutes the first time.
After that, every merge to `main` deploys automatically.

## What you will create

```
                 your team (Google accounts @yourdomain)            clients (anyone with a link)
                                │                                              │
                     Identity-Aware Proxy (login)                              │
                                │                                              │
          ┌─────────── Cloud Run: rfm-studio ───────────┐        ┌── Cloud Run: rfm-share ──┐
          │  the app · APP_MODE=full                     │        │  /r/<token> only         │
          │  service account: rfm-studio                 │        │  service account:        │
          │   · reads client data in BigQuery            │        │   rfm-share (read-only)  │
          │   · reads/writes the bucket                  │        └────────────┬─────────────┘
          └──────────────┬──────────────────┬────────────┘                     │
                         │                  │                                  │
                  BigQuery datasets    Cloud Storage bucket  ◄─────────────────┘
                  (client tables)      runs, reports, share links
```

Both services run the same image from this repository. The share service can only read published share links;
it cannot reach BigQuery or any run data.

| Thing | Name used below | Why |
|---|---|---|
| Cloud Storage bucket | `<project>-rfm-studio` | Saved runs, cached tables, reports, share links |
| Service account | `rfm-studio` | Identity of the app: BigQuery read + bucket read/write |
| Service account | `rfm-share` | Identity of the share service: bucket read-only |
| Cloud Run service | `rfm-studio` | The internal app, behind Google login (IAP) |
| Cloud Run service | `rfm-share` | Public share links |

Use one region for everything. `europe-north1` (Finland) is a good default for Nordic clients and data.

---

## 1 · Turn on the APIs

**APIs & Services → Enable APIs and services**, enable:
Cloud Run Admin API · Cloud Build API · Artifact Registry API · BigQuery API · BigQuery Storage API ·
Cloud Storage API · Identity-Aware Proxy API.

## 2 · Create the bucket

**Cloud Storage → Buckets → Create**

1. Name: `<project>-rfm-studio` (bucket names are global, so prefix your project ID).
2. Location type: **Region** → `europe-north1`.
3. Storage class: **Standard**.
4. Access control: **Uniform**. Keep **Enforce public access prevention** ticked.
5. Create.

Optional, to keep costs down: **Lifecycle → Add a rule → Delete object**, condition *Age 400 days*.
Old runs then clean themselves up.

## 3 · Create the two service accounts

**IAM & Admin → Service accounts → Create service account**

**`rfm-studio`** (the app)
1. Name `rfm-studio` → Create and continue.
2. Grant on the project: **BigQuery Read Session User** (lets the app stream table data; no queries are run). Done.
3. On the bucket (**Cloud Storage → your bucket → Permissions → Grant access**): principal
   `rfm-studio@<project>.iam.gserviceaccount.com`, role **Storage Object Admin**.
4. For every dataset with client data (**BigQuery → dataset → Sharing → Permissions → Add principal**):
   the same principal, role **BigQuery Data Viewer**. Datasets can live in other projects — grant it there.

**`rfm-share`** (share links)
1. Name `rfm-share` → Create and continue → no project roles → Done.
2. On the bucket: principal `rfm-share@<project>.iam.gserviceaccount.com`, role **Storage Object Viewer**.

## 4 · Deploy the app (`rfm-studio`)

**Cloud Run → Deploy container → Service** (or *Connect repository* on the Cloud Run start page).

1. Choose **Continuously deploy from a repository** → **Set up with Cloud Build**.
2. Repository provider **GitHub** → authenticate → repository `RiceBasmaty/rfmanalysis`.
3. Branch `^main$` · Build type **Dockerfile** · Source location `/Dockerfile` → Save.
4. Service name `rfm-studio`, region `europe-north1`.
5. **Authentication: Require authentication.**
6. **Billing: Instance-based** (the console may call it "CPU is always allocated").
   Reports are built in the background after the request returns, so the CPU must stay on.
7. **Scaling:** minimum instances `0`, **maximum instances `1`**.
   One instance is enough for a team and keeps job state consistent.
   Set minimum to `1` if you want no cold start (about 1–2 s otherwise), at the cost of an always-on instance.
8. Expand **Containers, volumes, networking, security**:
   * **Container → Settings:** port `8080`, memory **4 GiB**, CPU **2**, request timeout **3600** seconds.
   * **Container → Variables & secrets**, add:

     | Name | Value |
     |---|---|
     | `RFM_STORAGE` | `gs://<project>-rfm-studio` |
     | `RFM_BQ_PROJECT` | your project ID (pre-filled as the billing project on the Connect step) |

   * **Security → Service account:** `rfm-studio`.
9. Create. The first build takes 3–5 minutes. Follow it under **Cloud Build → History**.

**Turn on Google login (IAP)**

1. Open the service → **Security** tab → Authentication → tick **Identity-Aware Proxy (IAP)** → Save.
   If asked, configure the OAuth consent screen as **Internal**.
2. **Security → Identity-Aware Proxy** → select `rfm-studio` → **Add principal**:
   `domain:yourdomain.com` (or a Google group), role **IAP-secured Web App User**.
3. Open the service URL. You log in with your Google account and land on **RFM analyses**.

## 5 · Deploy the share service (`rfm-share`)

Repeat step 4 with these differences:

| Setting | Value |
|---|---|
| Service name | `rfm-share` |
| Authentication | **Allow public access**. If your organisation blocks `allUsers`, choose **Disable invoker IAM check** instead. |
| Billing | Request-based |
| Scaling | min `0`, max `3` |
| Memory / CPU | 512 MiB / 1 |
| Variables | `APP_MODE` = `share`, `RFM_STORAGE` = `gs://<project>-rfm-studio` |
| Service account | `rfm-share` |
| IAP | off |

Then copy the `rfm-share` URL (for example `https://rfm-share-abc123-lz.a.run.app`) and add it to **`rfm-studio`**:
**Edit & deploy new revision → Variables** → `RFM_SHARE_BASE_URL` = that URL → Deploy.

Open `<share URL>/healthz`. It should answer `{"ok":true,"mode":"share",...}`. Every other path except `/r/<token>` returns 404.

## 6 · First run

1. In the app: **Start a new analysis** → client name.
2. **Connect:** BigQuery → billing project + `project.dataset` → **Load tables** → check the matches → **Check columns**
   → fix any mapping → **Save and continue**.
3. **Settings → Save and run data check.** Review the findings, trim if needed.
4. **Build report → Share link.** Open the link in a private window: it must work without a login.

## Updating and rolling back

* Merge to `main` → Cloud Build builds and deploys both services automatically.
* Something wrong? **Cloud Run → service → Revisions →** pick the previous revision → **Manage traffic → 100 %**.
* Settings live in environment variables; change them with **Edit & deploy new revision**.

## Troubleshooting

| You see | Cause | Fix |
|---|---|---|
| "Could not list the tables" · 403 / permission | `rfm-studio` cannot see the dataset | Step 3: BigQuery Data Viewer on that dataset |
| "Could not read the … table" · readsessions | Missing Read Session User on the billing project | Step 3: BigQuery Read Session User |
| Job stuck, then "The job was interrupted" | CPU throttled after the request | Step 4.6: instance-based billing |
| Build fails in Cloud Build | Usually a dependency download | Retry the build from Cloud Build → History; check the log |
| Share link opens the login page | Link points to the app, not the share service | Set `RFM_SHARE_BASE_URL` (step 5) |
| "This link has expired" | Older than 90 days, or removed | Create a new link on the report page |
| "Table too large" | More than 20 M rows | Filter the table in BigQuery to the lookback window, or raise `RFM_MAX_ROWS` |

## Settings reference

| Variable | Default | Meaning |
|---|---|---|
| `RFM_STORAGE` | `./data` | `gs://bucket[/prefix]` in production |
| `APP_MODE` | `full` | `share` = only serve share links |
| `RFM_BQ_PROJECT` | — | Default billing project on the Connect step |
| `RFM_SHARE_BASE_URL` | this app's URL | Public URL of the share service |
| `RFM_SHARE_DAYS` | `90` | Default lifetime of a share link |
| `RFM_WORKERS` | `2` | Jobs that can run at the same time |
| `RFM_MAX_ROWS` | `20000000` | Largest BigQuery table the app will load |
| `RFM_STALE_MINUTES` | `20` | A job silent this long is shown as interrupted |
| `RFM_LOCAL_DATA_DIR` | — | Local development only: folder of CSV folders shown as "Sample data" |

## Security notes

* The app is only reachable through IAP, with your organisation's Google accounts.
* The app reads only the columns the analysis needs, and only through the read-only BigQuery Storage API.
* Share links use 192-bit random tokens, expire (90 days by default), can be removed at any time and are
  marked `noindex`. A link shows the report as it was when shared; rebuilding a report does not change old links.
* The shared page is one self-contained file with a strict Content-Security-Policy: it cannot load or send anything else.
* Client data stays in your project: BigQuery → the app → your bucket. Hashed emails appear only in the
  `customer_segments.csv` download, never in the report or the share link.
