# DEVLOG

Append-only, one entry per work session, newest at the bottom. Each entry covers: what I built, why I made these decisions (with the tradeoffs named), what I learned or got stuck on, what's next.

---

## Scaffold

**What I built**

The full repo skeleton before any real data: ingestion/ with a stub per source (Grailed, eBay, Depop, Vestiaire, The RealReal, 2nd Street, retail, trends, social, plus the orchestrator), resolution/ for the entity resolution pipeline, db/ for the hand-written schema, empty dbt_project/, features/, ml/, dashboard/, data/ split into raw (gitignored), fixtures, and reference, docs/, tests/, and the CI directory. Plus the machinery: README with the phase checklist at the top, this DEVLOG, .env.example covering Bright Data, Reddit, and eBay credentials, .gitignore, and thin Makefile, Dockerfile, requirements.txt, and pyproject.toml. Every Python module is a docstring stub saying what it will do and which phase builds it, so the tree reads as an architecture diagram from the first commit.

**Why these decisions**

requirements.txt starts with pytest and nothing else. Dependencies get added in the phase that first imports them. The alternative was pinning the whole imagined stack now, Splink, PySpark, LightGBM, the lot, and that turns into fiction the first time a library choice changes under measurement, which Phase 2 is explicitly designed to allow. The cost is that early commits do not install the eventual stack. I can live with that.

data/raw/ is excluded with a wildcard plus a kept .gitkeep, and fixtures live in their own tracked directory instead of inside raw with an exception rule. "Raw never enters git" is a rule I can state in one sentence and audit at a glance. "Raw is in git except when" is not.

Stub modules over empty files: a few docstrings will be wrong by the time their phase arrives, and I would rather correct a docstring than stare at a bare tree for eight phases.

Makefile and Dockerfile are deliberately thin. They get finalized in Phase 9 when there are actual stages to wire up. Writing them now means writing them twice.

**What I learned or got stuck on**

Nothing hard yet. One environment note worth recording: part of my workflow runs in a Linux dev container, so the virtual environment gets created on my machine, not in the session. .venv is gitignored and the setup commands live in the README.

**What's next**

Grailed ingestion. Before building anything, confirm the current realistic access path and write up the choice, including whatever ToS gray areas it touches.

---

## Ingestion, all nine sources

**What I built**

The full ingestion layer. A shared base module with the RawListing record, the fixture machinery, and a single Bright Data seam. Nine clients: Grailed, eBay, Depop, Vestiaire, The RealReal, 2nd Street, retail (SSENSE, DSM, Saks), Google Trends, and Reddit. An orchestrator that lands one timestamped JSON file per source in data/raw/. Nineteen tests that run with no credentials and no network. A full fixture run lands 57 records across all nine sources. I built Grailed and eBay first and verified them end to end before touching the rest, per the plan, but the whole phase ships as one commit since that's the rhythm now.

**Why these decisions**

Fixtures are platform-shaped payloads, not pre-parsed records. A Grailed fixture looks like an Algolia hit, a Depop fixture looks like the webapi response, and both go through the exact parser live data would. The alternative, fixtures in my own clean schema, would have been faster to write and would have tested nothing but the dataclass constructor. Cost: fixture authoring took real time, and when a live payload shape drifts the fixture has to drift with it.

Live mode is double-gated: INGEST_LIVE=1 plus per-source credentials. Without both, every client stays on fixtures. This means the default behavior is deterministic on any machine, including CI, and nobody accidentally hammers a platform because they happened to have creds in their env.

Per platform, the access calls and what they cost me:

Grailed has no API, but its search runs on Algolia and the search credentials its own frontend uses can query the index directly. Those values rotate, so they're env vars, not code. Grailed's ToS doesn't welcome scraping; this is public listing data at a polite rate, and I'm calling it what it is, a gray area.

eBay was the biggest surprise. The Finding API, which had findCompletedItems, was decommissioned in February 2025, and the Marketplace Insights API that replaced it is restricted to approved partners. There is no sanctioned free path to sold comps anymore. So actives come from the Browse API with proper OAuth, clean and official, and sold prices come from the public completed-listings search pages, parsed defensively. Gray area, documented, and not optional: sold prices are the ground truth the whole label depends on.

Depop has an unauthenticated JSON search endpoint its own site uses. ToS prohibits scraping, so it's low-volume and polite, and it's in the same gray bucket as Grailed.

Vestiaire and The RealReal sit behind DataDome and Cloudflare, and free scraping there isn't realistic at any useful volume. Both route through Bright Data Web Unlocker. That's the one part of the stack that costs money, roughly a dollar and a half per thousand requests, so a weekly pull of a few hundred pages stays under a dollar a month. One honest caveat: the live parsers for both target embedded page JSON and are best-effort until the first funded run. The fixtures and tests carry those modules until then.

2nd Street's US site is plain server-rendered HTML, so it's direct requests, no middleman. It also brings the best normalization problems in early: Japanese letter-grade conditions and centimeter shoe sizing, which Phase 2a has to handle anyway.

Retail: DSM's e-shop is Shopify, which exposes the public products.json endpoint, free and clean. SSENSE serves JSON from its own endpoints. Saks is behind Akamai, routes through Bright Data, and is the lowest priority of the three since SSENSE and DSM cover most of the watchlist.

Trends: the plan said pytrends, but pytrends was archived in April 2025 after years of 429 misery, so I went with trendspy, the successor its own maintainer points to. Every free Google Trends client is fragile by nature; if the trends signal earns its way into the model, this is the dependency most likely to break first.

Reddit via PRAW is the only fully clean source in the layer. Official API, free tier, no asterisks.

**What I learned or got stuck on**

The access landscape moved under the plan between writing it and building it: pytrends dead, eBay's Finding API gone. Researching each source before building it, instead of trusting the plan's assumptions, earned its keep on day one of real work.

**What's next**

Phase 2a, text-based entity resolution. The fixtures already carry the problem on purpose: the same Geobasket listed as "43", "US 10", "sz 10", and "27.5cm", conditions ranging from "B" to "Very good condition" to "used, some creasing". Normalize, block, match, measure.
