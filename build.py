#!/usr/bin/env python3
"""Build the FLIMKit website from the wikis of the org's public plugin repos.

Discovery   : GitHub API, public non-archived repos of the org that carry the
              `flimkit-plugin` topic (plus the core repo). Private repos are
              never seen, so they cannot leak into the site.
Content     : each repo's wiki (`<repo>.wiki.git`, cloned anonymously). A repo
              with no wiki falls back to its README.
Output      : static HTML in ./dist, deployed by .github/workflows/pages.yml.

Usage:  python build.py [--out dist] [--repos-file repos.json]
        --repos-file skips the API call (offline / testing); it takes the same
        JSON the API returns for `GET /orgs/{org}/repos`.
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

import markdown

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG = json.load(open(os.path.join(ROOT, 'config.json')))
ORG = CONFIG['org']
MD_EXT = ['fenced_code', 'tables', 'sane_lists', 'toc', 'attr_list', 'md_in_html']
IMG_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'}


# ---------------------------------------------------------------- discovery
def api(path):
    req = urllib.request.Request('https://api.github.com' + path,
                                 headers={'Accept': 'application/vnd.github+json'})
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def listRepos(repos_file):
    if repos_file:
        data = json.load(open(repos_file))
        return data['items'] if isinstance(data, dict) else data
    out, page = [], 1
    while True:
        chunk = api(f'/orgs/{ORG}/repos?type=public&per_page=100&page={page}')
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def selectRepos(repos):
    """Public, live, non-fork plugin repos. Core first, then alphabetical."""
    keep = []
    for r in repos:
        name = r['name']
        if r.get('private') or r.get('archived') or r.get('fork') or r.get('disabled'):
            continue
        if r.get('is_template') or name in CONFIG['exclude']:
            continue
        if name != CONFIG['core'] and CONFIG['topic'] not in (r.get('topics') or []):
            continue
        keep.append(r)
    keep.sort(key=lambda r: (r['name'] != CONFIG['core'], r['name'].lower()))
    return keep


# ------------------------------------------------------------------ fetching
def git(*args, cwd=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    return subprocess.run(['git', *args], cwd=cwd, env=env, capture_output=True, text=True)


def fetchWiki(repo, dest):
    url = f'https://github.com/{ORG}/{repo}.wiki.git'
    p = git('clone', '--depth', '1', url, dest)
    if p.returncode != 0:
        return False
    shutil.rmtree(os.path.join(dest, '.git'), ignore_errors=True)
    return any(f.endswith('.md') for f in os.listdir(dest))


def fetchReadme(repo, branch, dest):
    os.makedirs(dest, exist_ok=True)
    for name in ('README.md', 'readme.md', 'README.markdown'):
        url = f'https://raw.githubusercontent.com/{ORG}/{repo}/{branch}/{name}'
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                open(os.path.join(dest, 'Home.md'), 'wb').write(r.read())
            return True
        except Exception:
            continue
    return False


# ----------------------------------------------------------------- rendering
def pageKey(name):
    return re.sub(r'[\s_-]+', '-', urllib.parse.unquote(name).strip()).lower()


def pageTitle(filename):
    return filename[:-3].replace('-', ' ')


def sidebarOrder(wikidir, pages):
    """Order pages by the wiki's own _Sidebar.md when it has one."""
    side = os.path.join(wikidir, '_Sidebar.md')
    order = []
    if os.path.exists(side):
        text = open(side, encoding='utf-8').read()
        for target in re.findall(r'\]\(([^)#\s]+)', text) + re.findall(r'\[\[(?:[^\]|]*\|)?([^\]]+)\]\]', text):
            k = pageKey(target)
            if k in pages and k not in order:
                order.append(k)
    rest = sorted((k for k in pages if k not in order), key=lambda k: pages[k]['title'].lower())
    order += rest
    if 'home' in order:
        order.remove('home')
        order.insert(0, 'home')
    return order


def rewriteMarkdown(text, repo, keys, branch, is_readme):
    """Resolve [[wiki links]], wiki URLs and relative links to site URLs."""
    base = f'/plugins/{repo}/'

    def wikilink(m):
        label, target = m.group(1), m.group(2) or m.group(1)
        return f'[{label}]({target})'
    text = re.sub(r'\[\[([^\]|]+)(?:\|([^\]]+))?\]\]', wikilink, text)

    def link(m):
        bang, label, url = m.group(1), m.group(2), m.group(3).strip()
        frag = ''
        if '#' in url:
            url, frag = url.split('#', 1)
            frag = '#' + frag
        wiki_re = rf'^https?://github\.com/{ORG}/{re.escape(repo)}/wiki(?:/([^/?#]*))?$'
        w = re.match(wiki_re, url, re.I)
        if w:
            url = w.group(1) or 'Home'
        elif re.match(r'^([a-z][a-z0-9+.-]*:|//|/)', url, re.I) or (not url and frag):
            return m.group(0)
        k = pageKey(url[:-3] if url.lower().endswith('.md') else url)
        if not bang and k in keys:
            return f'[{label}]({base}{keys[k]}/{frag})'
        if is_readme or bang:
            kind = 'raw' if bang else 'blob'
            host = 'https://raw.githubusercontent.com' if bang else 'https://github.com'
            path = f'{ORG}/{repo}/{branch}/{url}' if bang else f'{ORG}/{repo}/blob/{branch}/{url}'
            if bang:
                return f'{bang}[{label}]({host}/{path}{frag})'
            return f'[{label}]({host}/{path}{frag})'
        if bang:  # wiki-local image: copied next to the page tree
            return f'{bang}[{label}]({base}{url})'
        return m.group(0)

    return re.sub(r'(!?)\[([^\]]*)\]\(([^)\s]+)\)', link, text)


def render(text):
    md = markdown.Markdown(extensions=MD_EXT, extension_configs={'toc': {'permalink': False}})
    return md.convert(text)


# ------------------------------------------------------------------- layout
def layout(title, body, nav_repos, crumb='', sidebar=''):
    plugins = ' &middot; '.join(
        f'<a href="/plugins/{html.escape(r["name"])}/">{html.escape(r["name"])}</a>' for r in nav_repos)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(CONFIG['tagline'])}">
<link rel="icon" href="/static/icon.png">
<link rel="stylesheet" href="/static/style.css"></head>
<body>
<header class="top"><div class="wrap">
<a class="brand" href="/"><img src="/static/icon.png" alt="" width="28" height="28">FLIMKit</a>
<nav><a href="/plugins/">Plugins</a>
<a href="/plugins/{CONFIG['core']}/">Docs</a>
<a href="https://github.com/{ORG}">GitHub</a></nav>
</div></header>
<main class="wrap{' has-side' if sidebar else ''}">
{sidebar}<article>{crumb}{body}</article>
</main>
<footer class="wrap"><p>Built from the public wikis of the
<a href="https://github.com/{ORG}">{ORG}</a> organisation. Edit a wiki and this site
follows within the hour. Plugins: {plugins or 'none yet'}.</p></footer>
</body></html>'''


def write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8').write(content)


def buildRepo(r, workdir, out, nav):
    repo, branch = r['name'], r.get('default_branch', 'main')
    src = os.path.join(workdir, repo)
    is_readme = False
    if r.get('has_wiki', True) and fetchWiki(repo, src):
        pass
    elif fetchReadme(repo, branch, src):
        is_readme = True
    else:
        print(f'  {repo}: no wiki and no README, listing only')
        return {'repo': r, 'pages': 0, 'source': 'none'}

    pages = {}
    for dp, _, files in os.walk(src):
        for f in files:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, src)
            if f.startswith('_') and f.endswith('.md'):
                continue  # _Sidebar / _Footer are wiki chrome
            if f.endswith('.md') and os.sep not in rel:
                pages[pageKey(f[:-3])] = {'file': full, 'title': pageTitle(f), 'slug': f[:-3]}
            elif os.path.splitext(f)[1].lower() in IMG_EXT:
                dst = os.path.join(out, 'plugins', repo, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(full, dst)
    if not pages:
        return {'repo': r, 'pages': 0, 'source': 'none'}
    keys = {k: p['slug'] for k, p in pages.items()}
    order = sidebarOrder(src, pages)
    srcname = 'README' if is_readme else 'wiki'

    for k, p in pages.items():
        text = open(p['file'], encoding='utf-8').read()
        body = render(rewriteMarkdown(text, repo, keys, branch, is_readme))
        links = ''.join(
            f'<li{" class=cur" if o == k else ""}><a href="/plugins/{repo}/{pages[o]["slug"] if o != "home" else ""}{"/" if o != "home" else ""}">'
            f'{html.escape(pages[o]["title"])}</a></li>' for o in order)
        side = f'<aside><h3>{html.escape(repo)}</h3><ul>{links}</ul></aside>' if len(pages) > 1 else ''
        crumb = (f'<p class="crumb"><a href="/plugins/">Plugins</a> / '
                 f'<a href="/plugins/{repo}/">{html.escape(repo)}</a>'
                 f'{"" if k == "home" else " / " + html.escape(p["title"])}'
                 f' &middot; <a href="https://github.com/{ORG}/{repo}{"" if is_readme else "/wiki/" + p["slug"]}">'
                 f'edit on GitHub</a></p>')
        title = f'{p["title"]} · {repo}' if k != 'home' else repo
        dest = os.path.join(out, 'plugins', repo, '' if k == 'home' else p['slug'], 'index.html')
        if k != 'home' and 'home' not in pages and k == order[0]:
            write(os.path.join(out, 'plugins', repo, 'index.html'), layout(title, body, nav, crumb, side))
        write(dest, layout(title, body, nav, crumb, side))
    print(f'  {repo}: {len(pages)} page(s) from {srcname}')
    return {'repo': r, 'pages': len(pages), 'source': srcname}


def buildIndexes(results, out, nav):
    def card(x):
        r = x['repo']
        n = html.escape(r['name'])
        desc = html.escape(r.get('description') or '')
        meta = f'{x["pages"]} page{"s" if x["pages"] != 1 else ""} · {x["source"]}' if x['pages'] else 'GitHub only'
        href = f'/plugins/{n}/' if x['pages'] else f'https://github.com/{ORG}/{n}'
        return (f'<a class="card" href="{href}"><h3>{n}</h3><p>{desc}</p>'
                f'<small>{html.escape(meta)}</small></a>')
    core = [x for x in results if x['repo']['name'] == CONFIG['core']]
    rest = [x for x in results if x['repo']['name'] != CONFIG['core']]
    grid = '<div class="grid">' + ''.join(card(x) for x in rest) + '</div>'
    write(os.path.join(out, 'plugins', 'index.html'), layout(
        'Plugins · FLIMKit', f'<h1>Plugins</h1><p>Every public FLIMKit add-on, discovered from the '
        f'<code>{CONFIG["topic"]}</code> topic.</p>{grid}', nav))
    home = (f'<section class="hero"><h1>FLIMKit</h1><p class="lead">{html.escape(CONFIG["tagline"])}</p>'
            f'<p><a class="btn" href="/plugins/{CONFIG["core"]}/">Read the docs</a> '
            f'<a class="btn ghost" href="https://github.com/{ORG}/{CONFIG["core"]}/releases/latest">Download</a></p></section>'
            + ('<h2>Core</h2><div class="grid">' + ''.join(card(x) for x in core) + '</div>' if core else '')
            + '<h2>Plugins</h2>' + grid)
    write(os.path.join(out, 'index.html'), layout('FLIMKit', home, nav))
    write(os.path.join(out, '404.html'), layout('Not found · FLIMKit',
          '<h1>Page not found</h1><p><a href="/">Back to the front page</a></p>', nav))
    write(os.path.join(out, '.nojekyll'), '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='dist')
    ap.add_argument('--repos-file')
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, 'static'), os.path.join(out, 'static'))
    repos = selectRepos(listRepos(a.repos_file))
    print(f'{len(repos)} public repo(s): ' + ', '.join(r['name'] for r in repos))
    with tempfile.TemporaryDirectory() as work:
        results = []
        for r in repos:
            try:
                results.append(buildRepo(r, work, out, repos))
            except Exception as e:  # one broken wiki must not take the site down
                print(f'  {r["name"]}: FAILED ({e})', file=sys.stderr)
                results.append({'repo': r, 'pages': 0, 'source': 'none'})
    buildIndexes(results, out, repos)
    print(f'site written to {out}')


if __name__ == '__main__':
    main()
