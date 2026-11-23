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
| 1 Requirements | An `.xlsx`/`.xlsm` compliance sheet: choose the sheet, the requirement / ID / section / customer-guidance columns, and the first and (optionally) last row to answer. Or a `.docx` specification, where the numbering comes from the headings: choose the heading to start at and the heading to stop before. |
| 2 Sources | The knowledge areas this response may use (XProtect, BriefCam, Husky IVO, …), plus folders and websites that belong to this tender only. |
| 3 Product & strategy | The product(s) to offer, product rules (e.g. "XProtect only, not BriefCam"), the sales team's win strategy, sales input files (mails, notes) and extra instructions. |
| 4 Output | The Excel columns to fill. Compliance can be written as text, or as an X per level (Fully / Widely / …). Optionally a Word template with placeholders. You can also edit the compliance scale here. |
| 5 Respond & review | Claude Code answers in batches. Open a row to see the quotes (verified or not), the links, why it is not fully compliant, the question to the customer and the product considered. **⤢** opens the reading pane: one requirement at a time, full size, with Previous/Next (Alt+←/→) through the current filter, e.g. all partly compliant rows. Edit and approve, then write Excel/Word and save the approved answers to the knowledge base. Edits are saved while you type. |

Each answer states **which product** it considered, and which module or licence delivers the function.

## Knowledge areas (shared on SharePoint)

The knowledge is split into **areas**: XProtect, BriefCam, Arcules, Husky IVO hardware, Technology partners and Milestone company info. You can add more. Each response chooses which areas it may use (step 2), so an XProtect-only offer never quotes BriefCam material. Earlier approved answers are only offered as precedents within the chosen areas.

Each area has its own folders, documentation websites and databases (**Knowledge base** tab):
- **Update** reads the sources again: changed files are re-indexed, deleted files and removed sources disappear.
- **Rebuild** starts from an empty area. Use it after a new release (point the website source to the new version first) or when old content must go (e.g. last year's annual report).
- **Documents** lists the area's content, so single documents can be removed.
- **Roll back** restores the version before the last change.

**Why it is safe on SharePoint.** In **Settings → Shared knowledge folder**, choose a folder in the synced Global Bid Desk library. SQLite is never opened on SharePoint itself:

```
<shared folder>\areas\<area>.db        published version (read-only for everybody)
<shared folder>\areas\<area>.prev.db   the version before (roll back)
<shared folder>\areas\<area>.json      sources, version, who built it and when
<shared folder>\areas\<area>.lock      present while someone changes the area
<shared folder>\answers\<year>\*.json  one file per saved answer
<shared folder>\register\*.json        one file per response: tender profile + answered requirements
<shared folder>\reports\*.docx|.md     reports written with Claude
```

- Everybody searches a local copy (`%LOCALAPPDATA%\RFPResponder\cache`). It is refreshed when a newer version has been published, and integrity-checked first, so a half-synced file is never used.
- A change takes the area's lock, builds locally, and then publishes the new file in one step. If someone else published meanwhile, the change is refused instead of overwriting theirs.
- Answers are separate small files, so two people saving at once never write the same file.
- File paths under your profile are stored as `%USERPROFILE%\...`, so a folder synced by one colleague resolves on every PC.

### Databases (MS SQL Server)

**Add database (MS SQL)** in an area connects to a SQL Server database, for example the Husky IVO product and certificate data. Enter the server, the database and a table, view or SELECT query (joins are fine), then click **Test and preview**. Each row the query returns becomes one document (`Column: value` lines), named by the title column. Answers can quote it, and the quote is checked against the row. **Update** reloads the rows: changed rows are re-indexed and rows that disappeared are removed.

- Only a single SELECT (or WITH … SELECT) is accepted. The connection asks for read-only intent and is always rolled back. Still, use an account that can only read.
- **My Windows account** (integrated sign-in) is recommended. For a SQL Server login, the password is stored only on the PC where it was entered, encrypted for that Windows user (DPAPI). It is never written to the shared folder. A colleague who updates the area enters the password once on their own PC.
- The driver is chosen automatically: ODBC Driver 18/17 for SQL Server if installed, otherwise the "SQL Server" driver built into Windows.

Folders and websites added in step 2 belong to that response only. They are indexed in a local database next to the project.

An old single-file knowledge base (v0.1) is offered for import on the Knowledge base tab. You pick an area per collection, and the earlier answers come along.

## Reporting

Every response feeds a shared **requirements register**. Colleagues in product management, security & compliance and marketing use it to see what customers ask for, where the products fall short and why.

**What is collected.** Each answered requirement is stored with:
- its compliance, the product considered, the answer, why it is not fully compliant, and who approved it;
- a classification that Claude adds while answering: the **topic**, whether it is **security-relevant**, the **standards** it names (ISO 27001, IEC 62443, NDAA, GDPR, …) and, for gaps, the **gap type** (not available, needs third party, on roadmap, …). Rows answered before this, or edited by hand, are classified by keyword rules.

Each tender also gets a **tender profile** (step 3, *Product & strategy*): vertical, country/region, end customer type, consultant/evaluator, partner, deadline, outcome, why the customer runs the tender, what concerns them, and key themes. **Suggest with Claude** fills it in from the requirements, for you to check.

**How it is shared.** About once a minute, a changed response is published as one file in `<shared folder>\register\`. Only its owner writes that file. Turn off *Share in the requirements register* for a confidential tender, and it is taken out. Deleting a response also removes it.

**The Reporting tab**
- Four views: **Product management** (gaps by topic and product, why), **Security & compliance** (security-relevant requirements, the standards requested, where we fall short), **Marketing** (verticals, regions, consultants, partners, outcomes, why customers tender and what concerns them) and **All requirements**. *Settings → My reporting focus* sets the view the tab opens on.
- Filter by text, compliance, topic, product, vertical, region, country, outcome, tender, period, security-relevant only and approved only. Click a bar to filter by it. Open a requirement to read the customer's wording, our answer and the gap.
- **Export** the filtered requirements and tender profiles to Excel (two sheets), CSV or JSON, to build your own knowledge hub.
- **Build report with Claude**: optionally ask a question. Claude reads the filtered data (and only that) and writes a report for the audience, with counts, examples by tender and requirement ID, and facts kept apart from interpretation. The report is saved as Word and Markdown in `<shared folder>\reports\`, where colleagues can open it under *Reports*. It uses your own Claude Code sign-in.

## How Claude Code is used

For each batch of requirements (5 by default) the app does three things:
1. It retrieves the best passages from the knowledge base and earlier approved answers.
2. It runs `claude -p` headless. The answer must follow a JSON schema. Claude gets only read-only tools: `Read`, `Grep` and `Glob` on your source folders (`--add-dir`), and `WebFetch` on the domains you allow. `--permission-mode dontAsk` denies anything else.
3. It verifies the quotes and links, then sets the status.

**Writing quality: one coherent document, not 300 isolated answers.**
- **Tender brief.** Before answering, Claude reads all the requirements once and writes a tender brief: what the customer is buying, how the document is built, recurring themes, which facts to state only once and where (offered product and edition, architecture…), and which requirements ask about the same thing. It is written again when the requirements change. See **Tender brief** on the review page.
- **What each batch gets.** The brief, the document outline, the related answers already given and the openings already used. The first batch runs alone, and each later batch sees every answer finished before it.
- **The rules Claude follows.**
  - Never restate the requirement. Start with the substance: what goes beyond the literal question, how it works, the limits.
  - Name a product, module or licence only where it adds information. Never open with it.
  - Refer to earlier answers ("see 4.2.1") instead of repeating them.
  - Vary the openings.
  - Write for the customer, never about the answer itself.
- **Consider.** Each answer can carry a short note: what the project should take into account and why it matters for the result. In Excel/Word it follows the answer as "Consider: …" or goes into its own column, and you can switch it off in step 4.
- **After answering.** Answers that still repeat the requirement, open like several others or open with the product name are flagged. Filter on **Style issues** and use **Answer checked again**.
- **The product name in front** of an answer is only added in Excel when the answer relies on something other than the products chosen for the tender.

**Word, Excel, PowerPoint and Outlook files.** Claude Code cannot read these formats directly. Before a run, each one in the allowed folders gets a readable text copy under `%LOCALAPPDATA%\RFPResponder\readable`, with the same relative path plus `.txt`. Claude can read and search those copies, and it cites the original file. Copies are only remade when the original changes.

For traceability, the prompts and raw answers are kept in `%LOCALAPPDATA%\RFPResponder\runs\<project>\<timestamp>\`.

The app uses your own Claude Code sign-in. Pick the model (opus / sonnet / haiku) and the effort in Settings. In testing, Sonnet answered 3 requirements in about 15 s for about $0.10.

## Install and run

RFP Responder runs on **Windows and macOS**. Every user needs:
- Python 3.11 or newer ([python.org](https://www.python.org/downloads/)).
- Claude Code, installed and signed in with their own account (`claude --version`).
- Microsoft Edge or Google Chrome for the app window. Without either, the app opens in the default browser.
- The Global Bid Desk SharePoint library synced with OneDrive, for the shared knowledge folder.

**Install or update.** The installer copies the app to a local folder, creates a private Python environment and adds a shortcut. It needs no admin rights. Run it again to update; settings, responses and caches are kept.

| | How | Installs to | Start from |
|---|---|---|---|
| Windows | Double-click `install\Install RFP Responder (Windows).bat` | `%LOCALAPPDATA%\RFPResponder` | Start menu or desktop: **RFP Responder** |
| Mac | Open Terminal, type `bash ` (with a space), drag `install/install_mac.command` into the window, press Enter | `~/Library/Application Support/RFPResponder` | Launchpad, Spotlight or `~/Applications`: **RFP Responder** |

Files synced from SharePoint lose their "executable" flag on a Mac, so the Mac installer is started with `bash` rather than by double-clicking. The app does not run from the SharePoint folder. That way nobody's Python environment or cache files are synced back to the library.

**For development**, run from the source folder: `start.bat` / `RFPResponder.pyw` on Windows, `bash start.command` on a Mac.

**Mac notes**
- **Shared paths:** paths inside synced libraries are stored by library name, so sources added on Windows are found on a Mac and the other way round. **Settings** lists the libraries found on the computer. If one is missing, sync it in OneDrive (or use *Add shortcut to My files*), or add its folder under *Extra library folders*.
- **File dialogs:** these use the macOS dialogs. The first time, macOS may ask to let the app control "System Events"; allowing it brings the dialog to the front.
- **Passwords:** database passwords are kept in the login Keychain.
- **Database sources:** these are optional on a Mac. They need `brew install unixodbc msodbcsql18` and `pip install pyodbc` in the app's environment. Windows integrated sign-in does not work from a Mac; use a SQL Server login. Areas built by Windows colleagues, including their database rows, work without any of this.

The app starts a local server on `127.0.0.1` (token-protected) and opens in an Edge or Chrome app window.

**Long breaks and shutdowns.** The server runs as long as the app window is open. Idle time, a locked screen or sleep do not stop it, and it never stops while a job runs. If the connection drops anyway, the window shows "Reconnecting…", waits, and repeats what it was doing, so nothing is lost. Edits are saved while you type. The address and token are remembered (`session.json` in the app's data folder), and so is the response, step and row you were on. So after the PC has been shut down, start RFP Responder again and you are back where you left off. A window that was left open reconnects by itself. Starting the app while it already runs just opens another window on the running server.

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
  extract.py             text from docx/xlsx/pptx/pdf/html/msg/eml with headings/pages
  kb.py                  SQLite + FTS5 store (one per area / project)
  library.py             knowledge areas: publish, local cache, lock, roll back, answers, import
  ingest.py              folder indexing, website reader (robots.txt, same path)
  sqlsrc.py              MS SQL Server source: read-only query guard, rows -> documents, DPAPI passwords
  readable.py            readable .txt copies of Office files for Claude Code
  register.py            requirements register: publish per response, local index, filters, classification, export
  reporting.py           report views, Claude-written reports (Markdown + Word), tender profile suggestion
  osdep.py               Windows/macOS differences: data folder, dialogs, browser, Keychain/DPAPI, synced libraries
  engine.py              prompt, Claude Code call, quote + link verification
  writers.py             Excel write-back (text or X-marks), Word template fill
  jobs.py                background jobs with progress/cancel
  web/                   UI: Apollo design tokens, Svensk Tenn radar background
install/                 installers: Windows (.bat/.ps1) and macOS (.command)
tools/                   background + template generators
templates/               sample Word template
```

The design follows the Apollo design system (Milestone XProtect UI): the dark theme, a P28 title bar and stage tabs like the RFP Analyser. Behind it is a Josef Frank / Svensk Tenn botanical wallpaper with a radar and transponder: questions go out as yellow Q blips, and the answers come back as rings.
