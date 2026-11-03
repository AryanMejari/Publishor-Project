# ISIRP1

ISIRP is a hobby research-publication website built with Flask and Firebase
Firestore. It accepts manuscripts, sends confirmation email, generates
certificates, and runs an automated academic screening pipeline.

## Local screening and optional AI

The screening pipeline works without an API key. It checks manuscript
structure, word count, citations, clarity, and methodology, then stores the
results in Firestore and creates a downloadable report.

To enable the optional Gemini review layer, set one of these environment
variables in `.env`:

```env
GEMINI_API_KEY=your-key
# Optional:
# GEMINI_MODEL=gemini-2.5-flash
```

Keys are never required for the local screening fallback and should not be
committed to Git.

## Verification

Each screened paper can be viewed at:

```text
/verify/<paper-id>
```

The verification page shows the paper metadata, screening status, and
SHA-256 hashes when available. The project is a hobby platform; its reports
are automated screening feedback, not accredited peer review.

## Research community

The `/community` page adds a person-to-person discussion layer without
changing the existing submission or dashboard login. Researchers create a
separate account, browse papers that completed screening, and submit
structured challenges containing a neutral title, a claim, and supporting
evidence. Challenges start as `pending` so a future moderator can approve
them; approved challenges can receive helpful votes, which award reputation
points and the `Evidence Builder` badge at 10 points.

Community data is stored in three additional Firestore collections:
`community_users`, `paper_challenges`, and `reputation_events`. Passwords are
stored as Werkzeug hashes, never as plaintext. Set `FLASK_SECRET_KEY` in
`.env` before hosting so community sessions remain stable across restarts. Set
`COMMUNITY_MODERATOR_EMAILS` to a comma-separated allowlist of trusted
moderator account emails; only those accounts can approve or reject pending
challenges through the moderation endpoint.

## Deploying on Render

1. Create a Render Web Service and connect the `AryanMejari/Publishor-Project`
   GitHub repository.
2. Select the `main` branch, choose the Python runtime, and use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn --bind 0.0.0.0:$PORT server:app`
3. Add every variable from `.env.example` in Render's Environment settings.
   For `FIREBASE_SERVICE_ACCOUNT_JSON`, paste the complete Firebase service
   account JSON as one-line JSON, not a local Windows file path.
4. Deploy and test `/`, `/community`, `/verify/<paper-id>`, and a Firestore
   login. Render's free service sleeps when idle and its local filesystem is
   ephemeral; Firebase Firestore preserves metadata, but uploaded files,
   certificates, and reports need Firebase Storage before production use.

   ## Deploying on Vercel

   Vercel is also supported through `api/index.py` and `vercel.json`.

   1. Import `AryanMejari/Publishor-Project` into Vercel and select the `main`
      branch.
   2. Leave the framework preset as **Other**. Vercel will detect the Python
      function from `api/index.py`.
   3. Add the variables from `.env.example` under **Project Settings →
      Environment Variables**. Paste the complete Firebase service-account JSON
      into `FIREBASE_SERVICE_ACCOUNT_JSON`; do not use a local file path.
   4. Deploy. Do not use the Gunicorn start command on Vercel; Vercel invokes
      `api/index.py` as the serverless function.
   5. Test `/`, `/community`, `/api/community/papers`, and a Firestore login.

   Vercel functions have execution-time and filesystem limits. The existing
   submission flow starts certificate, email, and review work in background
   threads and writes files locally, so those operations are not reliable after a
   serverless response. For a reliable Vercel deployment, move uploads,
   certificates, and reports to Firebase Storage and move long-running review
   work to a queued worker or scheduled service. Firestore metadata and the
   community features remain suitable for Vercel.
