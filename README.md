# flimkit.github.io

Source of the FLIMKit website. It holds no documentation of its own: every page is
pulled from the wiki of a public repo in the [FLIMKit](https://github.com/FLIMKit)
organisation.

## How a repo gets onto the site

A repo is listed when it is public, not archived, not a fork or template, and carries
the `flimkit-plugin` topic (the core `FLIMKit` repo is always included). Private repos
are invisible to the build. Rules live in `config.json`.

For each repo the build clones `<repo>.wiki.git` anonymously and renders it, using the
wiki's `_Sidebar.md` for page order and resolving `[[wiki links]]`. A repo without a
wiki falls back to its `README.md`.

## Freshness

`.github/workflows/pages.yml` rebuilds on push, hourly, manually, and on a
`wiki-updated` repository dispatch.

## Local preview

    pip install markdown
    python build.py --out dist && python -m http.server -d dist

Unauthenticated API calls are rate-limited to 60/hour; export `GITHUB_TOKEN` if you hit it.

## One-time setup

Settings > Pages > Source: **GitHub Actions**. GitHub Pages on a *private* repo needs a
paid plan (Pro/Team/Enterprise); on the free plan the repo must be public.

## Placeholder

The root `index.html` is a stand-in shown only until the first workflow deploy. The real
site (including its own `index.html`) is generated into `dist/` by `build.py` and
replaces it, so Pages must use the **GitHub Actions** source, not "Deploy from a branch".
