# Milestone RFP Responder

You send a question and the responder answers. This works like a radar transponder.

RFP Responder answers tender requirements the way the Global Bid Desk does:
- from evidence;
- for the product being offered;
- in the customer's own numbering.

It reads the requirements from the customer's Excel compliance sheet or Word specification. It answers each one with **Claude Code**, using your own background material. Every claim is checked against its source before anyone approves it.

> The answers can become legally binding in a tender. A requirement is marked compliant only when a quote from your sources supports it. The app looks for each quote word for word in the cited file or web page, and opens each link. Answers that fail either check are flagged **Needs review**.

## What it does

| Step | What you set |
|---|---|
| 1 Requirements | An `.xlsx`/`.xlsm` compliance sheet: choose the sheet and the requirement / ID / section / customer-guidance columns. Or a `.docx` specification, where the numbering comes from the headings. |
| 2 Sources | Background folders (add as many as you need with **+**). Indexing makes them searchable and lets every quote be verified. You can also add websites: the app reads them page by page into the knowledge base. |
| 3 Product & strategy | The product(s) to offer, product rules (e.g. "XProtect only, not BriefCam"), the sales team's win strategy, sales input files (mails, notes) and extra instructions. |
| 4 Output | The Excel columns to fill. Compliance can be written as text, or as an X per level (Fully / Widely / …). Optionally a Word template with placeholders. You can also edit the compliance scale here. |
| 5 Respond & review | Claude Code answers in batches. Open a row to see the quotes (verified or not), the links, gaps, the question to the customer and the product considered. Edit and approve the answer, then write Excel/Word and save the approved answers to the knowledge base. |

Each answer states **which product** it considered, and which module or licence delivers the function.

## Knowledge base (local or shared)

There is one SQLite file with full-text search. It holds:
- indexed documents and web pages;
- every answer that was saved, draft or approved.

Earlier approved answers are offered to Claude as *precedents* for consistent wording. The facts must still be confirmed in a source.

To share it with the team, go to **Settings → Knowledge base file → Choose folder…** and pick a shared drive or a synced SharePoint folder. Everyone who points to the same file builds and uses the same knowledge.

The database runs in rollback-journal mode with long lock timeouts, so it works on network shares for a bid team. For many simultaneous writers, a server database is the next step.

## How Claude Code is used

For each batch of requirements (5 by default) the app does three things:
1. It retrieves the best passages from the knowledge base and earlier approved answers.
2. It runs `claude -p` headless. The answer must follow a JSON schema. Claude gets only read-only tools: `Read`, `Grep` and `Glob` on your source folders (`--add-dir`), and `WebFetch` on the domains you allow. `--permission-mode dontAsk` denies anything else.
3. It verifies the quotes and links, then sets the status.

For traceability, the prompts and raw answers are kept in `%LOCALAPPDATA%\RFPResponder\runs\<project>\<timestamp>\`.

The app uses your own Claude Code sign-in. Pick the model (opus / sonnet / haiku) and the effort in Settings. In testing, Sonnet answered 3 requirements in about 15 s for about $0.10.

## Install and run

Requirements: Windows, Python 3.11+, Claude Code installed and signed in (`claude --version`).

```bat
pip install -r requirements.txt
start.bat            (or double-click RFPResponder.pyw)
```

The app starts a local server on `127.0.0.1` (random port, token-protected) and opens in an Edge app window. The server exits a few minutes after the window is closed.

To rebuild the background art, run `python tools/make_background.py`. To rebuild the sample Word template, run `python tools/make_template.py`.

## Word template placeholders

- Anywhere in the document: `{{project}} {{customer}} {{date}} {{author}} {{products}} {{summary}}`
- In one table row, which is repeated for every answered requirement: `{{id}} {{section}} {{requirement}} {{compliance}} {{compliance_label}} {{explanation}} {{product}} {{responsible}} {{evidence}} {{links}} {{confidence}} {{gaps}} {{question}}`
- A template without such a row gets a compliance table added at the end.

See `templates/Compliance_Response_Template.docx`.

## Layout

```
RFPResponder.pyw         start without console
responder/
  __main__.py            server + app window
  server.py              JSON API (token-protected, localhost only)
  config.py              settings, projects (%LOCALAPPDATA%\RFPResponder)
  reqs.py                read requirements from Excel / Word
  extract.py             text from docx/xlsx/pdf/html/msg… with headings/pages
  kb.py                  SQLite + FTS5 knowledge base, precedents
  ingest.py              folder indexing, website reader (robots.txt, same path)
  engine.py              prompt, Claude Code call, quote + link verification
  writers.py             Excel write-back (text or X-marks), Word template fill
  jobs.py                background jobs with progress/cancel
  web/                   UI: Apollo design tokens, Svensk Tenn radar background
tools/                   background + template generators
templates/               sample Word template
```

The design follows the Apollo design system (Milestone XProtect UI): the dark theme, a P28 title bar and stage tabs like the RFP Analyser. Behind it is a Josef Frank / Svensk Tenn botanical wallpaper with a radar and transponder: questions go out as yellow Q blips, and the answers come back as rings.
