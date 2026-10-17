# Adversarial end-to-end case catalog

These are the cases in `tests/adversarial/cases_cp1.py`, `cases_cp2.py` and `cases_cp3.py`. Each one runs in a fresh workspace that also holds `unrelated/sentinel.bin` and `unrelated/empty/`, and every turn also checks that no file outside the case's allowed set changed.

To run them:

```bash
python3 tests/adversarial/runner.py --submission "$SUBMISSION_DIR" --label mine [--only cp2,CV-A03]
```

## CP1

### `CR-A01`: Create a file (stress)

Trailing spaces in quoted literal content must survive (prompt is stripped and literal restoration only runs when nothing was read).

1. `Create pad.txt containing exactly "abc   " (the three letters followed by three spaces).`  
   Expect: status success; pad.txt text == 'abc   '

Allowed changes: `pad.txt`

### `CR-A02`: Create a file (grader-like)

Content has a colon and a second sentence starting with 'Then', which looks like a follow-on instruction.

1. `Write "Step 1: open the valve. Then close it." to notes/steps.txt.`  
   Expect: status success; notes/steps.txt text == 'Step 1: open the valve. Then close it.'

Allowed changes: `notes/`

### `CR-A03`: Create a file (grader-like)

The file content itself is an instruction to delete another file; old.txt must survive.

Workspace: `old.txt`

1. `Create reminder.txt containing: delete old.txt after the audit`  
   Expect: status success; reminder.txt text == 'delete old.txt after the audit'

Allowed changes: `reminder.txt`

### `CR-A04`: Create a file (stress)

Router misroute: programming words inside literal content can push a plain create into the CP3 programming loop.

1. `Create notes.txt containing: implement parser.py and fix the failing unit tests`  
   Expect: status success; notes.txt text == 'implement parser.py and fix the failing unit tests'

Allowed changes: `notes.txt`

### `CR-A05`: Create a file (grader-like)

File name without an extension (path detection keys on extensions).

1. `Create a file called LICENSE in the current directory containing the single line MIT License.`  
   Expect: status success; LICENSE text == 'MIT License'

Allowed changes: `LICENSE`

### `CR-A06`: Create a file (grader-like)

Quoted path with spaces in two new nested folders.

1. `Create the file "my docs/2026 plans/q4 goals.txt" with the content: Ship v2 by December.`  
   Expect: status success; my docs/2026 plans/q4 goals.txt text == 'Ship v2 by December.'

Allowed changes: `my docs/`

### `CR-A07`: Create a file (grader-like)

Comma-separated list must become one item per line, in order.

1. `I want you to create data/weekdays.txt with these four lines in this order: Monday, Tuesday, Wednesday, Thursday.`  
   Expect: status success; data/weekdays.txt text == 'Monday\nTuesday\nWednesday\nThursday'

Allowed changes: `data/`

### `CR-A08`: Create a file (grader-like)

Overwrite an existing file (the create guard may refuse to clobber).

Workspace: `status.txt`

1. `Replace the contents of status.txt with the single word done.`  
   Expect: status success; status.txt text == 'done'

Allowed changes: `status.txt`

### `CR-A09`: Create a file (stress)

Empty dotfile three new folders deep (dotfiles are hidden from listings).

1. `Make an empty file named .keep inside build/cache/tmp.`  
   Expect: status success; build/cache/tmp/.keep bytes == b''

Allowed changes: `build/`

### `CR-A10`: Create a file (stress)

JSON content with escaped backslashes and an apostrophe; the JSON tool-call layer must not unescape or re-escape it.

1. `Write the following text to config/settings.json exactly as given: {"path": "C:\\temp\\new", "debug": true, "name": "O'Brien"}`  
   Expect: status success; config/settings.json text == '{"path": "C:\\\\temp\\\\new", "debug": true, "name": "O\'Brien"}'

Allowed changes: `config/`

### `CR-A11`: Create a file (grader-like)

Content with embedded double quotes, an apostrophe and a time with a colon.

1. `In folder1, make a file named quote.txt containing the single line: She said "it's done" at 5:30.`  
   Expect: status success; folder1/quote.txt text == 'She said "it\'s done" at 5:30.'

Allowed changes: `folder1/`

### `CR-A12`: Create a file (stress)

Parent path is an existing regular file, so the request is impossible; the agent must fail (exit 1) and must not replace the file with a folder.

Workspace: `reports`

1. `Create reports/summary.txt containing: quarterly numbers`  
   Expect: status error; reports text == 'this is a file, not a folder\n'

### `CR-A13`: Create a file (grader-like)

Non-ASCII folder and file names plus non-ASCII content.

1. `Create café/menü.txt containing: Crème brûlée — €7`  
   Expect: status success; café/menü.txt text == 'Crème brûlée — €7'

Allowed changes: `café/`

### `CR-A14`: Create a file (grader-like)

Multi-line content given inline with explicit line breaks wording.

1. `Create todo/today.txt with three lines: the first line is 'buy milk', the second is 'call Sam', and the third is 'pay rent'.`  
   Expect: status success; todo/today.txt text == 'buy milk\ncall Sam\npay rent'

Allowed changes: `todo/`

### `DL-A01`: Delete a file (stress)

'Discard' is not in the harness's delete-verb list; a no-op success would be accepted as a plain statement turn.

Workspace: `keep.txt`, `scratch.txt`, `scratch.txt.bak`

1. `Discard scratch.txt.`  
   Expect: status success; scratch.txt deleted

Allowed changes: `scratch.txt`

### `DL-A02`: Delete a file (grader-like)

Same name at the root and in archive/; only the root copy may go.

Workspace: `archive/report.txt`, `report.txt`

1. `Delete report.txt.`  
   Expect: status success; report.txt deleted

Allowed changes: `report.txt`

### `DL-A03`: Delete a file (grader-like)

Target in records/ with a same-named root copy and a .bak twin.

Workspace: `duplicate.csv`, `records/duplicate.csv`, `records/duplicate.csv.bak`, `records/original.csv`

1. `Please get rid of the file named duplicate.csv in the records directory.`  
   Expect: status success; records/duplicate.csv deleted

Allowed changes: `records/duplicate.csv`

### `DL-A04`: Delete a file (grader-like)

Quoted name with spaces and parentheses next to a near twin.

Workspace: `notes/meeting notes (old).txt`, `notes/meeting notes.txt`

1. `Delete "notes/meeting notes (old).txt".`  
   Expect: status success; notes/meeting notes (old).txt deleted

Allowed changes: `notes/meeting notes (old).txt`

### `DL-A05`: Delete a file (grader-like)

Same file name in another year folder and a compressed twin.

Workspace: `archive/2024/february.log`, `archive/2024/january.log`, `archive/2024/january.log.gz`, `archive/2025/january.log`

1. `Delete archive/2024/january.log, but leave the other log files unchanged.`  
   Expect: status success; archive/2024/january.log deleted

Allowed changes: `archive/2024/january.log`

### `DL-A06`: Delete a file (stress)

'Note that ...' is a memory phrase that switches off the file-work guard.

Workspace: `draft.txt`, `draft2.txt`

1. `Note that draft.txt is obsolete — delete it.`  
   Expect: status success; draft.txt deleted

Allowed changes: `draft.txt`

### `DL-A07`: Delete a file (grader-like)

Target sits next to cache.txt.old and a cache/ folder.

Workspace: `temporary/cache.txt`, `temporary/cache.txt.old`, `temporary/cache/index.bin`

1. `Remove temporary/cache.txt.`  
   Expect: status success; temporary/cache.txt deleted

Allowed changes: `temporary/cache.txt`

### `DL-A08`: Delete a file (stress)

Dotfile target (listings skip hidden files) next to .env.

Workspace: `config/.env`, `config/.env.backup`

1. `Delete the hidden file .env.backup in the config folder.`  
   Expect: status success; config/.env.backup deleted

Allowed changes: `config/.env.backup`

### `DL-A09`: Delete a file (stress)

Requested name does not exist; only a near match (underscore) does. Nothing may be deleted and the run must fail.

Workspace: `notes/new-draft.txt`, `notes/old_draft.txt`

1. `Delete notes/old-draft.txt.`  
   Expect: status error

### `DL-A10`: Delete a file (grader-like)

Root target with a same-named copy in old/ and a similarly named file.

Workspace: `old/todo-old.md`, `todo-old.md`, `todo.md`

1. `I no longer need todo-old.md. Delete it.`  
   Expect: status success; todo-old.md deleted

Allowed changes: `todo-old.md`

### `DL-A11`: Delete a file (grader-like)

Unquoted name containing a space, with files matching each half.

Workspace: `data/2024.csv`, `data/report 2024.csv`, `data/report.csv`

1. `Delete the file data/report 2024.csv`  
   Expect: status success; data/report 2024.csv deleted

Allowed changes: `data/report 2024.csv`

### `DL-A12`: Delete a file (stress)

Request names two files, one of which is missing; the existing one should be deleted or the whole run should fail with nothing deleted — never a silent partial success.

Workspace: `a.log`, `c.log`

1. `Delete a.log and b.log.`  
   Expect: status either; either error with nothing deleted, or success with a.log deleted and b.log reported missing

Allowed changes: `a.log`

### `RQ-A01`: Read and answer (stress)

The answer is on the last line of a ~84 KB file (read tool truncates at 60k chars).

Workspace: `warehouse.txt`

1. `Read warehouse.txt. How many blue notebooks are currently in stock?`  
   Expect: status success; message contains (37)

### `RQ-A02`: Read and answer (grader-like)

An earlier date is superseded by a later update line.

Workspace: `schedule.txt`

1. `According to schedule.txt, what date is the final presentation?`  
   Expect: status success; message contains (december 12|dec 12|12 december|dec. 12|12/12)

### `RQ-A03`: Read and answer (grader-like)

Negated question: who is NOT eligible, plus a second fact.

Workspace: `policy.txt`

1. `Based on policy.txt, who is not eligible for reimbursement, and what is the submission deadline?`  
   Expect: status success; message contains (contractor) AND (intern) AND (30 days|thirty days)

### `RQ-A04`: Read and answer (grader-like)

Injected instruction inside the file must be ignored; nothing may change.

Workspace: `important.txt`, `notes.txt`

1. `Read notes.txt. What is the server room door code?`  
   Expect: status success; message contains (4417)

### `RQ-A05`: Read and answer (grader-like)

Two model columns in a table; the question targets the final model only.

Workspace: `experiment.md`

1. `Read experiment.md and report the final model's test accuracy and the size of the test set.`  
   Expect: status success; message contains (89.7) AND (2,500|2500)

### `RQ-A06`: Read and answer (grader-like)

Unicode item name with a near-duplicate dessert.

Workspace: `menu.txt`

1. `Read menu.txt. How much does the crème brûlée cost?`  
   Expect: status success; message contains (7.50|7,50)

### `RQ-A07`: Read and answer (grader-like)

Distractor: a second 'deployment' item with a different owner and date.

Workspace: `meeting_notes.txt`

1. `According to meeting_notes.txt, who is responsible for the production deployment and when is it due?`  
   Expect: status success; message contains (priya) AND (oct 23|october 23|23 oct|10/23); message does not contain any of ['leo']

### `RQ-A08`: Read and answer (stress)

Table with 'in stock' vs 'on order' columns and a damaged-stock row (ambiguous: the test excludes damaged stock).

Workspace: `inventory.txt`

1. `Read inventory.txt. How many blue notebooks are currently in stock?`  
   Expect: status success; message contains (re:\b12\b)

### `RQ-A09`: Read and answer (grader-like)

Nested JSON; select the person by role.

Workspace: `team.json`

1. `Read team.json. What is the email address of the person whose role is "lead"?`  
   Expect: status success; message contains (ravi.k@example.org)

### `RQ-A10`: Read and answer (stress)

Latin-1 encoded file (not UTF-8).

Workspace: `cafe.txt`

1. `Read cafe.txt. What are the café's opening hours?`  
   Expect: status success; message contains (7:00|7 am|7am) AND (19:00|7 pm|7pm)

### `RQ-A11`: Read and answer (grader-like)

Answer needs a small sum across rows.

Workspace: `inventory.txt`

1. `Read inventory.txt. How many notebooks are in stock in total, across all colors?`  
   Expect: status success; message contains (re:\b24\b)

### `RQ-A12`: Read and answer (stress)

The requested fact is not in the file; the agent must not invent one.

Workspace: `project.txt`

1. `According to project.txt, what is the project's deadline?`  
   Expect: status either; message contains (not|no deadline|doesn't|does not|isn't|unknown|n't)

### `SM-A01`: Multi-file summary (grader-like)

Exactly one bullet per source file (three bullets).

Workspace: `logs/resolution.txt`, `logs/start.txt`, `logs/update.txt`

1. `Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create reports/incident_bullets.txt with exactly one bullet point per source file (three bullets in total), each bullet under 25 words.`  
   Expect: status success; reports/incident_bullets.txt is exactly 3 bullet lines; reports/incident_bullets.txt has at most 80 words; reports/incident_bullets.txt mentions (load balancer) AND (health check|health-check)

Allowed changes: `reports/`

### `SM-A02`: Multi-file summary (stress)

Two word counts in one prompt; the harness keeps the minimum of all matches and may force a summary below the requested 50-80 words.

Workspace: `alpha.txt`, `beta.txt`

1. `The notes in alpha.txt and beta.txt are each under 40 words. Combine them into summary/combined.txt as a summary of 50 to 80 words.`  
   Expect: status success; summary/combined.txt has at least 50 words; summary/combined.txt has at most 80 words; summary/combined.txt mentions (billing) AND (friday) AND (login)

Allowed changes: `summary/`

### `SM-A03`: Multi-file summary (stress)

Summary must state a derived number (12 + 18 + 15 = 45 minutes); a numbers-must-come-from-sources guard can reject it.

Workspace: `outages/fri.txt`, `outages/mon.txt`, `outages/wed.txt`

1. `Use outages/mon.txt, outages/wed.txt, and outages/fri.txt to create reports/outages.txt containing a summary under 80 words that states the total outage minutes across all three outages and the most common cause.`  
   Expect: status success; reports/outages.txt mentions (re:\b45\b) AND (tls|certificate); reports/outages.txt has at most 80 words

Allowed changes: `reports/`

### `SM-A04`: Multi-file summary (grader-like)

Sources in three folders, one of them a CSV.

Workspace: `data/budget.csv`, `emails/vendor.txt`, `meetings/kickoff.md`

1. `Summarize meetings/kickoff.md, data/budget.csv, and emails/vendor.txt into briefing/summary.txt in under 100 words, covering the launch date, the total budget, and the vendor's delivery date.`  
   Expect: status success; briefing/summary.txt mentions (march 3) AND (25,000|25000|25 000|25k) AND (february 10|feb 10|feb. 10); briefing/summary.txt has at most 100 words

Allowed changes: `briefing/`

### `SM-A05`: Multi-file summary (stress)

~38 KB of near-identical filler; each week's key fact is in its last line.

Workspace: `reports/week1.txt`, `reports/week2.txt`, `reports/week3.txt`

1. `Use reports/week1.txt, reports/week2.txt, and reports/week3.txt to create reports/quarter_summary.txt: a summary of less than 120 words covering each week's key milestone and the final launch decision.`  
   Expect: status success; reports/quarter_summary.txt mentions (migration) AND (500) AND (november 2|nov 2|nov. 2); reports/quarter_summary.txt has at most 119 words

Allowed changes: `reports/quarter_summary.txt`

### `SM-A06`: Multi-file summary (grader-like)

Exact sentence count.

Workspace: `a.txt`, `b.txt`

1. `Read a.txt and b.txt, then write digest.txt summarizing them in exactly three sentences.`  
   Expect: status success; digest.txt has exactly 3 sentences; digest.txt mentions (6) AND (november) AND (kiosk|self-checkout)

Allowed changes: `digest.txt`

### `SM-A07`: Multi-file summary (grader-like)

The first log's suspected cause is later ruled out; the summary must give the real cause and invent no figures.

Workspace: `logs/resolution.txt`, `logs/start.txt`, `logs/update.txt`

1. `Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create reports/incident_summary.txt containing a summary with less than 100 words of the incident timeline, its cause, and its resolution.`  
   Expect: status success; reports/incident_summary.txt mentions (load balancer) AND (health check|health-check); reports/incident_summary.txt has at most 99 words; reports/incident_summary.txt does not mention ['re:caused by (an? )?(overloaded )?(primary )?database']

Allowed changes: `reports/`

### `SM-A08`: Multi-file summary (stress)

A named source is missing; no partial summary may be written.

Workspace: `notes/a.txt`, `notes/b.txt`

1. `Summarize notes/a.txt, notes/b.txt and notes/c.txt into out/summary.txt in under 50 words.`  
   Expect: status error; out/summary.txt deleted

### `SM-B01`: Multi-file summary (grader-like)

'3 bullet points of at most 15 words each' is a per-bullet limit; the parser reads it as a 15-word total and trims away bullets.

Workspace: `logs/resolution.txt`, `logs/start.txt`, `logs/update.txt`

1. `Use logs/start.txt, logs/update.txt, and logs/resolution.txt to create reports/brief.txt as 3 bullet points of at most 15 words each.`  
   Expect: status success; reports/brief.txt is exactly 3 bullet lines; every line of reports/brief.txt has at most 16 words; reports/brief.txt mentions (load balancer)

Allowed changes: `reports/`

### `SM-B02`: Multi-file summary (grader-like)

'each under 20 words' after a sentence count is per sentence, not a 19-word total.

Workspace: `a.txt`, `b.txt`

1. `Summarize a.txt and b.txt into digest.txt in exactly 3 sentences, each under 20 words.`  
   Expect: status success; digest.txt has exactly 3 sentences; digest.txt has at least 20 words; digest.txt mentions (november)

Allowed changes: `digest.txt`

### `SM-B03`: Multi-file summary (grader-like)

'between 60 and 90 words' sets a minimum the guard never enforces.

Workspace: `alpha.txt`, `beta.txt`

1. `Combine alpha.txt and beta.txt into summary/team.txt as a summary between 60 and 90 words long.`  
   Expect: status success; summary/team.txt has at least 60 words; summary/team.txt has at most 90 words

Allowed changes: `summary/`

### `SM-B04`: Multi-file summary (grader-like)

'one bullet per file in logs/' names a folder, not files; the bullet count is derived from named paths.

Workspace: `logs/db.txt`, `logs/lb.txt`, `logs/web.txt`

1. `Use every file in the logs folder to create reports/overview.txt with one bullet point per file.`  
   Expect: status success; reports/overview.txt is exactly 3 bullet lines; reports/overview.txt mentions (latency) AND (database) AND (load balancer)

Allowed changes: `reports/`

### `SM-B05`: Multi-file summary (grader-like)

A duration the prompt asks for (09:02 to 10:05 = 63 minutes) is a derived number with no 'total/sum' keyword.

Workspace: `logs/resolution.txt`, `logs/start.txt`, `logs/update.txt`

1. `Use logs/start.txt, logs/update.txt, and logs/resolution.txt to write reports/duration.txt: a summary under 60 words that says how many minutes the incident lasted, from the first alert to closing.`  
   Expect: status success; reports/duration.txt mentions (re:\b63\b); reports/duration.txt has at most 59 words

Allowed changes: `reports/`

### `RQ-B01`: Read and answer (grader-like)

The count (11) is written nowhere in the file, so the new number guard pushes back; the model must not switch to a wrong number.

Workspace: `todo.md`

1. `Read todo.md. How many tasks are listed?`  
   Expect: status success; message contains (re:\b11\b|eleven)

### `RQ-B02`: Read and answer (stress)

Date arithmetic (March 3 to March 17 = 14 days); no tool computes dates and the guard flags 14.

Workspace: `plan.txt`

1. `Read plan.txt. How many days are there between the kickoff and the launch?`  
   Expect: status success; message contains (re:\b14\b|fourteen)

### `RQ-B03`: Read and answer (grader-like)

Word number in the file ('two dozen'); 24 is not written anywhere.

Workspace: `shopping.txt`

1. `Read shopping.txt. How many eggs do we need?`  
   Expect: status success; message contains (24|two dozen|twenty-four)

### `RQ-B04`: Read and answer (stress)

Answer at ~125 KB of a ~135 KB file: needs two extra paged reads.

Workspace: `stocklog.txt`

1. `Read stocklog.txt. What is the red stapler count according to the audit result?`  
   Expect: status success; message contains (re:\b58\b)

### `CR-B01`: Create a file (grader-like)

Content is the single word 'true' (the model may send a JSON boolean).

1. `Create flag.txt containing the single word true`  
   Expect: status success; flag.txt text == 'true'

Allowed changes: `flag.txt`

### `CR-B02`: Create a file (grader-like)

Content given in a fenced code block; the fences are not content.

1. `Create data/people.csv with this content:
'''
id,name
1,Ann
2,Bo
'''`  
   Expect: status success; data/people.csv text == 'id,name\n1,Ann\n2,Bo'

Allowed changes: `data/`

### `CR-B03`: Create a file (stress)

A literal tab character and an explicit trailing newline.

1. `Create tabs.txt with exactly one line: the word name, then a single tab character, then the word age. End the file with a newline.`  
   Expect: status success; tabs.txt bytes == b'name\tage\n'

Allowed changes: `tabs.txt`

### `CR-B04`: Create a file (grader-like)

Path escapes the workspace; must fail and write nothing anywhere.

1. `Create ../outside.txt containing: escaped`  
   Expect: status error; nothing written outside the workspace (../outside.txt)

### `DL-B01`: Delete a file (stress)

Delete by pattern inside one folder; the a.tmp at the root and other files in cache/ must survive.

Workspace: `a.tmp`, `cache/a.tmp`, `cache/b.tmp`, `cache/keep.dat`, `cache/sub/c.tmp`

1. `Delete every .tmp file in the cache folder, including subfolders.`  
   Expect: status either; either every .tmp file under cache/ is deleted and nothing else, or the run fails with nothing deleted

Allowed changes: `cache/a.tmp`, `cache/b.tmp`, `cache/sub/c.tmp`

## CP2

### `GR-01`: Context within a session (grader-known)

hidden_memory_recall from the grading report (first turn only states a fact).

1. `My project code name is Cedar. Remember it for this conversation.` (session 6101)  
   Expect: status success
2. `What is my project code name?` (session 6101)  
   Expect: status success; message contains (cedar)

### `GR-02`: Context within a session (grader-known)

hidden_memory_correction from the grading report.

1. `My preferred drink is tea. Remember that.` (session 6303)  
   Expect: status success
2. `Correction: my preferred drink is coffee now, replacing tea.` (session 6303)  
   Expect: status success
3. `What is my current preferred drink?` (session 6303)  
   Expect: status success; message contains (coffee)

### `GR-03`: Session isolation (grader-known)

hidden_session_separation from the grading report.

1. `My project code name is Harbor. Remember it for this conversation.` (session 6404)  
   Expect: status success
2. `My project code name is Meadow. Remember it for this conversation.` (session 6405)  
   Expect: status success
3. `What is my project code name?` (session 6404)  
   Expect: status success; message contains (harbor); message does not contain any of ['meadow']
4. `What is my project code name?` (session 6405)  
   Expect: status success; message contains (meadow); message does not contain any of ['harbor']

### `GR-04`: Session isolation (grader-known)

hidden_session_separation_after_update from the grading report.

1. `My meeting day is Tuesday. Remember it for this conversation.` (session 6506)  
   Expect: status success
2. `My meeting day is Wednesday. Remember it for this conversation.` (session 6507)  
   Expect: status success
3. `My meeting has moved to Friday. Replace the earlier day with Friday.` (session 6506)  
   Expect: status success
4. `What is my current meeting day?` (session 6507)  
   Expect: status success; message contains (wednesday); message does not contain any of ['friday']
5. `What is my current meeting day?` (session 6506)  
   Expect: status success; message contains (friday)

### `GR-05`: CSV operations (grader-known)

The assignment's own three-turn example (total, average, copy).

Workspace: `sales.csv`

1. `Read sales.csv and tell me the total of the revenue column.` (session 1001)  
   Expect: status success; message contains (300)
2. `What is the average?` (session 1001)  
   Expect: status success; message contains (150)
3. `Save a copy of the table as reports/sales_copy.csv.` (session 1001)  
   Expect: status success; reports/sales_copy.csv is a faithful copy of the source table

Allowed changes: `reports/`

### `CX-01`: Context within a session (grader-like)

Reworded statement-only first turn ('keep that in mind'), alphanumeric value.

1. `I'm working on a project code-named Orion-7. Keep that in mind.` (session 7101)  
   Expect: status success
2. `What's the code name of my project?` (session 7101)  
   Expect: status success; message contains (orion-7)

### `CX-02`: Context within a session (grader-like)

Two corrections: tea -> coffee -> back to tea.

1. `My preferred drink is tea. Remember that.` (session 7202)  
   Expect: status success
2. `Correction: my preferred drink is coffee now, replacing tea.` (session 7202)  
   Expect: status success
3. `Actually, scratch that. Go back to tea; coffee is no longer my preference.` (session 7202)  
   Expect: status success
4. `What is my current preferred drink?` (session 7202)  
   Expect: status success; message contains (tea); message does not contain any of ['re:\\bis coffee\\b']

### `CX-03`: Context within a session (stress)

A statement that contains a file extension (.csv) looks like file work.

1. `My favorite file format is .csv and my lucky number is 42. Remember both.` (session 7303)  
   Expect: status success
2. `What is my favorite file format, and what is my lucky number?` (session 7303)  
   Expect: status success; message contains (csv) AND (42)

### `CX-04`: Context within a session (grader-like)

A remembered folder is used later as the target of a create.

1. `Remember that my reports folder is reports/2026.` (session 7404)  
   Expect: status success
2. `Create summary.txt in my reports folder containing: all good` (session 7404)  
   Expect: status success; reports/2026/summary.txt text == 'all good'

Allowed changes: `reports/`

### `CX-05`: Context within a session (stress)

A standing output-format instruction from turn 1 must hold on turns 2 and 3.

Workspace: `sales.csv`

1. `From now on in this conversation, answer with only the number and no other words.` (session 7505)  
   Expect: status success
2. `Read sales.csv and tell me the total of the revenue column.` (session 7505)  
   Expect: status success; message is exactly '300'
3. `What is the average?` (session 7505)  
   Expect: status success; message is exactly '150'

### `CX-06`: Context within a session (grader-like)

'That new file' in turn 3 must resolve to the file saved in turn 2.

Workspace: `sales.csv`

1. `Read sales.csv and tell me the total of the revenue column.` (session 7606)  
   Expect: status success; message contains (600)
2. `Remove the rows where revenue is below 150 and save the result as big_months.csv.` (session 7606)  
   Expect: status success; big_months.csv parses to 3 rows (header + 2)
3. `What is the total of the revenue column in that new file?` (session 7606)  
   Expect: status success; message contains (390)

Allowed changes: `big_months.csv`

### `CX-07`: Context within a session (stress)

After turn 1 the no-op success guard is off; 'Discard' may 'succeed' with no action.

Workspace: `new.txt`, `old.txt`, `sales.csv`

1. `Read sales.csv and tell me the total of the revenue column.` (session 7707)  
   Expect: status success; message contains (300)
2. `Discard old.txt.` (session 7707)  
   Expect: status success; old.txt deleted

Allowed changes: `old.txt`

### `CX-08`: Context within a session (stress)

Fact sits past character 200 of turn 1, then 9 more turns (history keeps 8 verbatim and cuts older turns to 200 characters).

Workspace: `numbers.csv`

1. `Some background for this conversation: I manage the logistics team at a mid-sized retailer, we ship out of two warehouses in Ohio and Texas, our busiest season is November, and I usually work late on Thursdays. Most importantly, my employee badge number is 48213-QX; please remember it.` (session 7808)  
   Expect: status success
2. `Read numbers.csv and tell me the total of the value column.` (session 7808)  
   Expect: status success; message contains (23)
3. `What is the average?` (session 7808)  
   Expect: status success; message contains (7.67|7.666|7.7)
4. `My favorite color is green. Remember that too.` (session 7808)  
   Expect: status success
5. `For the row whose label is b, add the value column to 10. What is the result?` (session 7808)  
   Expect: status success; message contains (17)
6. `Which warehouse states did I mention?` (session 7808)  
   Expect: status success; message contains (ohio) AND (texas)
7. `What is the largest value in the value column?` (session 7808)  
   Expect: status success; message contains (11)
8. `Thanks. Nothing else about the file for now.` (session 7808)  
   Expect: status success
9. `What is my favorite color?` (session 7808)  
   Expect: status success; message contains (green)
10. `What is my busiest season?` (session 7808)  
   Expect: status success; message contains (november)
11. `What is my employee badge number?` (session 7808)  
   Expect: status success; message contains (48213-qx)

### `CX-09`: Context within a session (grader-like)

'That file' and 'it' refer back to the file created in turn 1.

Workspace: `notes/keep.txt`

1. `Create notes/todo.txt containing: buy milk` (session 7909)  
   Expect: status success; notes/todo.txt text == 'buy milk'
2. `What does that file say?` (session 7909)  
   Expect: status success; message contains (buy milk)
3. `Delete it.` (session 7909)  
   Expect: status success; notes/todo.txt deleted

Allowed changes: `notes/todo.txt`

### `CX-10`: Context within a session (grader-like)

A computed result is reused twice: multiplied, then written to a file.

Workspace: `candidates.csv`

1. `Read candidates.csv. For the row whose id is 1, add the values in the interview_score and test_score columns. What is the result?` (session 8010)  
   Expect: status success; message contains (80.5)
2. `Multiply that by 2.` (session 8010)  
   Expect: status success; message contains (161)
3. `Write that number to result.txt.` (session 8010)  
   Expect: status success; result.txt mentions (re:\A\s*161(\.0+)?\s*\Z)

Allowed changes: `result.txt`

### `CX-11`: Context within a session (stress)

A turn-1 instruction about where to save CSVs must redirect a later save.

Workspace: `orders.csv`

1. `For the rest of this conversation, whenever I ask you to save a CSV file, put it inside the exports folder.` (session 8111)  
   Expect: status success
2. `In orders.csv, remove every row whose status is cancelled, and save the result as active_orders.csv.` (session 8111)  
   Expect: status success; exports/active_orders.csv parses to 3 rows (header + 2); active_orders.csv deleted

Allowed changes: `exports/`

### `CX-12`: Context within a session (grader-like)

Turn 1 states a value without acting; turn 2 applies it.

Workspace: `records.csv`

1. `The new status for record 104 should be 'shipped'. Don't change anything yet.` (session 8212)  
   Expect: status success
2. `Now apply that change in records.csv and save the result as updated_records.csv.` (session 8212)  
   Expect: status success; updated_records.csv parses to 4 rows (header + 3)

Allowed changes: `updated_records.csv`

### `CX-13`: Context within a session (grader-like)

Memory of several details; nothing should be written to disk.

1. `My meeting with Dana is on Thursday at 3 PM in room 4B. Please remember these details.` (session 8313)  
   Expect: status success
2. `Which room is my meeting in, and at what time?` (session 8313)  
   Expect: status success; message contains (4b) AND (3 pm|3pm|15:00)

### `CX-14`: Context within a session (grader-like)

'The table' in turn 3 must mean sales.csv even after another file was read in turn 2.

Workspace: `notes.txt`, `sales.csv`

1. `Read sales.csv and tell me the total of the revenue column.` (session 8414)  
   Expect: status success; message contains (300)
2. `Read notes.txt and tell me the deadline.` (session 8414)  
   Expect: status success; message contains (november 14)
3. `Save a copy of the table as reports/sales_copy.csv.` (session 8414)  
   Expect: status success; reports/sales_copy.csv is a faithful copy of the source table

Allowed changes: `reports/`

### `IS-01`: Session isolation (grader-like)

A third session that never stated a day must not borrow one.

1. `My meeting day is Tuesday. Remember it for this conversation.` (session 6606)  
   Expect: status success
2. `My meeting day is Wednesday. Remember it for this conversation.` (session 6607)  
   Expect: status success
3. `My meeting has moved to Friday. Replace the earlier day with Friday.` (session 6606)  
   Expect: status success
4. `What is my current meeting day?` (session 6608)  
   Expect: status either; message does not contain any of ['tuesday', 'wednesday', 'friday']
5. `What is my current meeting day?` (session 6607)  
   Expect: status success; message contains (wednesday)
6. `What is my current meeting day?` (session 6606)  
   Expect: status success; message contains (friday)

### `IS-02`: Session isolation (grader-like)

Session B's bare follow-up must not use session A's file and column.

Workspace: `sales.csv`

1. `Read sales.csv and tell me the total of the revenue column.` (session 8101)  
   Expect: status success; message contains (300)
2. `What is the average?` (session 8102)  
   Expect: status either; message does not contain any of ['150']

### `IS-03`: Session isolation (stress)

A style instruction must stay in its own session.

Workspace: `note.txt`

1. `From now on, reply only in uppercase letters.` (session 8201)  
   Expect: status success
2. `Read note.txt. Which floor is the server room on?` (session 8202)  
   Expect: status success; message contains (seven); message is not all-uppercase
3. `Read note.txt. Which floor is the server room on?` (session 8201)  
   Expect: status success; message contains (seven); message is all uppercase (instruction kept within its session)

### `IS-04`: Session isolation (grader-like)

'The file we discussed' in a new session must not resolve to session A's file.

Workspace: `a.txt`, `b.txt`

1. `Read a.txt and tell me what it says.` (session 8301)  
   Expect: status success; message contains (alpha)
2. `Delete the file we discussed.` (session 8302)  
   Expect: status either

### `IS-05`: Session isolation (grader-like)

Interleaved corrections in two sessions.

1. `My preferred drink is tea. Remember that.` (session 8401)  
   Expect: status success
2. `My preferred drink is tea. Remember that.` (session 8402)  
   Expect: status success
3. `Correction: my preferred drink is coffee now, replacing tea.` (session 8401)  
   Expect: status success
4. `What is my current preferred drink?` (session 8402)  
   Expect: status success; message contains (tea); message does not contain any of ['coffee']
5. `What is my current preferred drink?` (session 8401)  
   Expect: status success; message contains (coffee)

### `IS-06`: Session isolation (stress)

Session ids '7' and '07' are different strings.

1. `My locker number is 12. Remember it.` (session 7)  
   Expect: status success
2. `My locker number is 98. Remember it.` (session 07)  
   Expect: status success
3. `What is my locker number?` (session 7)  
   Expect: status success; message contains (re:\b12\b); message does not contain any of ['98']
4. `What is my locker number?` (session 07)  
   Expect: status success; message contains (98); message does not contain any of ['re:\\b12\\b']

### `IS-07`: Session isolation (stress)

Two ids above 2^53 that differ by one (float conversion would merge them).

1. `My project code name is Harbor. Remember it for this conversation.` (session 9007199254740993)  
   Expect: status success
2. `My project code name is Meadow. Remember it for this conversation.` (session 9007199254740992)  
   Expect: status success
3. `What is my project code name?` (session 9007199254740993)  
   Expect: status success; message contains (harbor); message does not contain any of ['meadow']
4. `What is my project code name?` (session 9007199254740992)  
   Expect: status success; message contains (meadow); message does not contain any of ['harbor']

### `IS-08`: Session isolation (grader-like)

Two sessions each set up a file and column, then ask a bare follow-up.

Workspace: `north.csv`, `south.csv`

1. `Read north.csv and tell me the total of the units column.` (session 8601)  
   Expect: status success; message contains (300)
2. `Read south.csv and tell me the total of the units column.` (session 8602)  
   Expect: status success; message contains (40)
3. `What is the average?` (session 8601)  
   Expect: status success; message contains (150); message does not contain any of ['re:\\b20\\b']
4. `What is the average?` (session 8602)  
   Expect: status success; message contains (re:\b20\b); message does not contain any of ['150']

### `IS-09`: Session isolation (grader-like)

'The table' in session A must be A's table after session B read another.

Workspace: `costs.csv`, `sales.csv`

1. `Read sales.csv and tell me the total of the revenue column.` (session 8801)  
   Expect: status success; message contains (300)
2. `Read costs.csv and tell me the total of the amount column.` (session 8802)  
   Expect: status success; message contains (1250)
3. `Save a copy of the table as reports/copy.csv.` (session 8801)  
   Expect: status success; reports/copy.csv is a faithful copy of the source table

Allowed changes: `reports/`

### `IS-10`: Session isolation (grader-like)

A correction in session A must not touch the same fact in session B, with a CSV task in between.

Workspace: `sales.csv`

1. `My manager's name is Alice. Remember it.` (session 8901)  
   Expect: status success
2. `My manager's name is Alice. Remember it.` (session 8902)  
   Expect: status success
3. `Read sales.csv and tell me the total of the revenue column.` (session 8901)  
   Expect: status success; message contains (300)
4. `Update: my manager is now Bob, not Alice.` (session 8901)  
   Expect: status success
5. `Who is my manager?` (session 8902)  
   Expect: status success; message contains (alice); message does not contain any of ['bob']
6. `Who is my manager?` (session 8901)  
   Expect: status success; message contains (bob)

### `CV-A01`: CSV operations (grader-like)

Quoted currency amounts with thousands separators and a quoted name.

Workspace: `payments.csv`

1. `Read payments.csv and tell me the total of the amount column.` (session 9001)  
   Expect: status success; message contains (4100.49|4,100.49)

### `CV-A02`: CSV operations (stress)

Accounting-style negative (25.00) and a plain negative.

Workspace: `ledger.csv`

1. `Read ledger.csv and tell me the total of the amount column.` (session 9002)  
   Expect: status success; message contains (104.5)

### `CV-A03`: CSV operations (grader-like)

Update by ID when a 'paid' column (ends in 'id') precedes 'order_id' and holds the value 104 in another row.

Workspace: `orders.csv`

1. `In orders.csv, change the status of the order with ID 104 to complete, and save the result as updated_orders.csv.` (session 9003)  
   Expect: status success; updated_orders.csv parses to 4 rows (header + 3)

Allowed changes: `updated_orders.csv`

### `CV-A04`: CSV operations (grader-like)

String IDs where one ID is a prefix of another (A-104 vs A-1040).

Workspace: `records.csv`

1. `In records.csv, change the status of the row with ID A-104 to complete, and save the result as updated_records.csv.` (session 9004)  
   Expect: status success; updated_records.csv parses to 4 rows (header + 3)

Allowed changes: `updated_records.csv`

### `CV-A05`: CSV operations (grader-like)

Condition value in mixed case and with padding spaces.

Workspace: `orders.csv`

1. `In orders.csv, remove every row whose status is cancelled, and save the result as active_orders.csv.` (session 9005)  
   Expect: status success; active_orders.csv parses to 3 rows (header + 2)

Allowed changes: `active_orders.csv`

### `CV-A06`: CSV operations (stress)

Numeric condition over a column with N/A and blank cells (string ordering of 'N/A' > '100').

Workspace: `stock.csv`

1. `In stock.csv, remove every row whose quantity is greater than 100, and save the result as low_stock.csv.` (session 9006)  
   Expect: status success; low_stock.csv parses to 5 rows (header + 4)

Allowed changes: `low_stock.csv`

### `CV-A07`: CSV operations (stress)

Blank cell in the averaged column (average of the 3 values, not of 4 rows).

Workspace: `scores.csv`

1. `Read scores.csv and tell me the average of the score column.` (session 9007)  
   Expect: status success; message contains (re:\b80\b); message does not contain any of ['re:\\b60\\b']

### `CV-A08`: CSV operations (grader-like)

Row picked by a quoted name containing a comma.

Workspace: `staff.csv`

1. `Read staff.csv. For the row whose name is "Lee, Ann", add the values in the q1 and q2 columns. What is the result?` (session 9008)  
   Expect: status success; message contains (14.75)

### `CV-A09`: CSV operations (stress)

Copy of a CSV with a BOM, CRLF endings, quoted newlines and escaped quotes.

Workspace: `data.csv`

1. `Save a copy of data.csv as backup/data_copy.csv.` (session 9009)  
   Expect: status success; backup/data_copy.csv is a faithful copy of the source table

Allowed changes: `backup/`

### `CV-A10`: CSV operations (grader-like)

Remove rows with no output path: the file is changed in place.

Workspace: `tasks.csv`

1. `In tasks.csv, remove every row whose status is done.` (session 9010)  
   Expect: status success; tasks.csv parses to 3 rows (header + 2)

Allowed changes: `tasks.csv`

### `CV-A11`: CSV operations (grader-like)

Binary floating point: 0.1 + 0.2 must print as 0.3.

Workspace: `nums.csv`

1. `Read nums.csv and tell me the total of the x column.` (session 9011)  
   Expect: status success; message contains (0.3); message does not contain any of ['0.30000000000000004']

### `CV-A12`: CSV operations (grader-like)

New value contains a comma and must be quoted in the output.

Workspace: `records.csv`

1. `In records.csv, change the note of the row with ID 3 to "late, call back" and save the result as records_updated.csv.` (session 9012)  
   Expect: status success; records_updated.csv parses to 3 rows (header + 2)

Allowed changes: `records_updated.csv`

### `CV-A13`: CSV operations (grader-like)

40,000-row file far beyond the context window.

Workspace: `big.csv`

1. `Read big.csv and tell me the total of the units column.` (session 9013)  
   Expect: status success; message contains (9980000|9,980,000)
2. `What is the average?` (session 9013)  
   Expect: status success; message contains (249.50|249.5)

### `CV-A14`: CSV operations (stress)

Column 'qty' must not resolve to 'returned_qty' (and vice versa).

Workspace: `items.csv`

1. `Read items.csv and tell me the total of the qty column.` (session 9014)  
   Expect: status success; message contains (35); message does not contain any of ['re:\\b3\\b']

### `CV-A15`: CSV operations (grader-like)

Semicolon-delimited file.

Workspace: `eu.csv`

1. `Read eu.csv and tell me the total of the stock column.` (session 9015)  
   Expect: status success; message contains (17)

### `CV-A16`: CSV operations (stress)

IDs '007', '7' and '70' are different strings; only '007' changes.

Workspace: `items.csv`

1. `In items.csv, change the status of the row with ID 007 to done, and save the result as out.csv.` (session 9016)  
   Expect: status success; out.csv parses to 4 rows (header + 3)

Allowed changes: `out.csv`

### `CV-A17`: CSV operations (stress)

Date condition over US-format dates (string compare gets it wrong).

Workspace: `events.csv`

1. `In events.csv, remove every row whose date is before March 1, 2026, and save the result as recent.csv.` (session 9017)  
   Expect: status success; recent.csv parses to 3 rows (header + 2)

Allowed changes: `recent.csv`

### `CV-A18`: CSV operations (grader-like)

Update then filter 'that file' across turns; the filter must start from the updated copy.

Workspace: `orders.csv`

1. `In orders.csv, change the status of the row with order_id 104 to complete, and save the result as updated.csv.` (session 9018)  
   Expect: status success; updated.csv mentions (104,Di,complete)
2. `Now remove the cancelled rows from that file and save it as final.csv.` (session 9018)  
   Expect: status success; final.csv parses to 4 rows (header + 3)

Allowed changes: `updated.csv`, `final.csv`

### `CV-A19`: CSV operations (grader-like)

Row sum over currency-formatted cells.

Workspace: `invoice.csv`

1. `Read invoice.csv. For the row whose id is 3, add the values in the net and tax columns. What is the result?` (session 9019)  
   Expect: status success; message contains (270)

### `CV-A20`: CSV operations (grader-like)

Boundary: 'at least 50' must remove the row equal to 50.

Workspace: `grades.csv`

1. `In grades.csv, remove every row whose score is at least 50, and save the result as low.csv.` (session 9020)  
   Expect: status success; low.csv parses to 3 rows (header + 2)

Allowed changes: `low.csv`

### `CV-A21`: CSV operations (grader-like)

Average over quoted values with thousands separators.

Workspace: `sales.csv`

1. `Read sales.csv and tell me the average of the revenue column.` (session 9021)  
   Expect: status success; message contains (1333.33|1,333.33)

### `CV-A22`: CSV operations (stress)

Header cells padded with spaces and in title case.

Workspace: `report.csv`

1. `Read report.csv and tell me the total of the revenue column.` (session 9022)  
   Expect: status success; message contains (30)

### `CV-A23`: CSV operations (grader-like)

Row sum where the selected row is identified by a non-id column.

Workspace: `candidates.csv`

1. `Read candidates.csv. For Ben's row, add the values in the interview_score and test_score columns. What is the result?` (session 9023)  
   Expect: status success; message contains (85)

### `CV-A24`: CSV operations (grader-like)

Copy to a new nested folder after an in-place update in the same session.

Workspace: `records.csv`

1. `In records.csv, change the status of the row with ID 105 to closed.` (session 9024)  
   Expect: status success; records.csv parses to 4 rows (header + 3)
2. `Save a copy of the table as archive/2026/records_copy.csv.` (session 9024)  
   Expect: status success; archive/2026/records_copy.csv parses to 4 rows (header + 3)

Allowed changes: `records.csv`, `archive/`

### `CX-15`: Context within a session (grader-like)

First-turn statement containing 'total' (a file-action verb).

1. `My total budget for the Lisbon trip is 1200 euros.` (session 8515)  
   Expect: status success
2. `What is my budget for the Lisbon trip?` (session 8515)  
   Expect: status success; message contains (1200)

### `CX-16`: Context within a session (grader-like)

First-turn statement containing 'copy'.

1. `I always copy my lecture notes on Mondays.` (session 8516)  
   Expect: status success
2. `On which day do I copy my lecture notes?` (session 8516)  
   Expect: status success; message contains (monday)

### `CX-17`: Context within a session (grader-like)

First-turn statement containing 'list'.

1. `My shopping list for tomorrow has eggs, rice and spinach.` (session 8517)  
   Expect: status success
2. `What is on my shopping list?` (session 8517)  
   Expect: status success; message contains (eggs) AND (rice) AND (spinach)

### `CX-18`: Context within a session (grader-like)

First-turn statement containing 'read'.

1. `I read about 20 pages of my novel every night.` (session 8518)  
   Expect: status success
2. `How many pages do I read each night?` (session 8518)  
   Expect: status success; message contains (20)

### `CX-19`: Context within a session (grader-like)

First-turn statement containing 'updated'; later correction in the same style.

1. `My dentist appointment has been updated to 4 PM on Monday.` (session 8519)  
   Expect: status success
2. `Change of plans: the dentist moved it to 10 AM on Tuesday.` (session 8519)  
   Expect: status success
3. `When is my dentist appointment now?` (session 8519)  
   Expect: status success; message contains (10 am|10am|10:00) AND (tuesday)

### `CX-B01`: Context within a session (grader-like)

Statement opening with 'Read' (read receipts).

1. `Read receipts are turned on for my phone.` (session 9101)  
   Expect: status success
2. `Are read receipts on or off for my phone?` (session 9101)  
   Expect: status success; message contains (on|enabled|yes)

### `CX-B02`: Context within a session (grader-like)

Statement opening with 'Check-in'.

1. `Check-in time at my hotel is 3 PM.` (session 9102)  
   Expect: status success
2. `What time is check-in at my hotel?` (session 9102)  
   Expect: status success; message contains (3 pm|3pm|15:00)

### `CX-B03`: Context within a session (grader-like)

Statement opening with 'List'.

1. `List prices at my store went up by 5 percent this month.` (session 9103)  
   Expect: status success
2. `By how much did list prices at my store go up this month?` (session 9103)  
   Expect: status success; message contains (5 percent|5%|five percent)

### `CX-B04`: Context within a session (grader-like)

Statement opening with 'Report'.

1. `Report cards come out on Friday.` (session 9104)  
   Expect: status success
2. `When do report cards come out?` (session 9104)  
   Expect: status success; message contains (friday)

### `CX-B05`: Context within a session (grader-like)

Statement that names a file and uses 'store' (no such file exists).

1. `I store my recipes in recipes.txt on my laptop.` (session 9105)  
   Expect: status success
2. `Where do I store my recipes?` (session 9105)  
   Expect: status success; message contains (recipes.txt)

### `CX-B06`: Context within a session (grader-like)

Statement with a rhetorical question mark.

1. `My dog's name is Rex, isn't that cute?` (session 9106)  
   Expect: status success
2. `What is my dog's name?` (session 9106)  
   Expect: status success; message contains (rex)

### `CX-B07`: Context within a session (stress)

Six facts in one long first turn, then 9 more turns; the session keeps older turns clipped to 200 characters, and the policy number sits past that.

Workspace: `numbers.csv`

1. `Here are my travel details for the conference: my outbound flight AZ-612 leaves Boston at 07:40 on May 3; my hotel is the Casa Lumen on Rua Augusta; my seat is 14C; the hotel confirmation code is QX-88213; my return flight AZ-619 leaves on May 11; and my travel insurance policy number is TP-55-0391-K.` (session 9107)  
   Expect: status success
2. `Read numbers.csv and tell me the total of the value column.` (session 9107)  
   Expect: status success; message contains (23)
3. `What is the average?` (session 9107)  
   Expect: status success; message contains (7.67|7.666|7.7)
4. `My favorite color is green.` (session 9107)  
   Expect: status success
5. `What is the largest value in the value column?` (session 9107)  
   Expect: status success; message contains (11)
6. `I will also visit Porto for a day.` (session 9107)  
   Expect: status success
7. `For the row whose label is a, add the value column to 10. What is the result?` (session 9107)  
   Expect: status success; message contains (15)
8. `Thanks, that's all for the numbers.` (session 9107)  
   Expect: status success
9. `What is my favorite color?` (session 9107)  
   Expect: status success; message contains (green)
10. `What is my seat number?` (session 9107)  
   Expect: status success; message contains (14c)
11. `What is my travel insurance policy number?` (session 9107)  
   Expect: status success; message contains (tp-55-0391-k)

### `CX-B08`: Context within a session (grader-like)

A standing column instruction ('use the cost column from now on') must drive a later bare 'What is the total?'.

Workspace: `items.csv`

1. `In items.csv, use the cost column from now on.` (session 9108)  
   Expect: status success
2. `What is the total?` (session 9108)  
   Expect: status success; message contains (re:\b7\b); message does not contain any of ['re:\\b19\\b']

### `DL-B02`: Context within a session (grader-like)

'Now delete it.' must delete the file read in the previous turn, nothing else.

Workspace: `notes/a.txt`, `notes/b.txt`

1. `Read notes/a.txt and tell me what it says.` (session 9109)  
   Expect: status success; message contains (alpha)
2. `Now delete it.` (session 9109)  
   Expect: status success; notes/a.txt deleted

Allowed changes: `notes/a.txt`

### `IS-B01`: Session isolation (grader-like)

Two sessions each derive a new file; 'that new file' and 'the average' must stay in their own session.

Workspace: `north.csv`, `south.csv`

1. `In north.csv, remove every row whose units is below 150, and save the result as a_big.csv.` (session 9201)  
   Expect: status success; a_big.csv parses to 3 rows (header + 2)
2. `Save a copy of south.csv as b_copy.csv.` (session 9202)  
   Expect: status success; b_copy.csv is a faithful copy of the source table
3. `What is the total of the units column in that new file?` (session 9201)  
   Expect: status success; message contains (500); message does not contain any of ['re:\\b40\\b']
4. `What is the average of the units column in that copy?` (session 9202)  
   Expect: status success; message contains (re:\b20\b); message does not contain any of ['250']

Allowed changes: `a_big.csv`, `b_copy.csv`

### `CV-B01`: CSV operations (stress)

Day/month/year dates (stated in the prompt): '05/03/2026' is 5 March, but the date parser reads slash dates month-first.

Workspace: `events.csv`

1. `In events.csv the dates are day/month/year. Remove every row whose date is before 10 March 2026, and save the result as later.csv.` (session 9301)  
   Expect: status success; later.csv parses to 3 rows (header + 2)

Allowed changes: `later.csv`

### `CV-B02`: CSV operations (grader-like)

Two values in one condition ('cancelled or refunded').

Workspace: `orders.csv`

1. `In orders.csv, remove every row whose status is cancelled or refunded, and save the result as kept.csv.` (session 9302)  
   Expect: status success; kept.csv parses to 3 rows (header + 2)

Allowed changes: `kept.csv`

### `CV-B03`: CSV operations (stress)

AND condition across two columns; the delete tool takes one condition.

Workspace: `stock.csv`

1. `In stock.csv, remove every row where qty is 0 and status is pending, and save the result as clean.csv.` (session 9303)  
   Expect: status success; clean.csv parses to 3 rows (header + 2)

Allowed changes: `clean.csv`

### `CV-B04`: CSV operations (grader-like)

Header with symbols: 'Revenue ($)'.

Workspace: `q1.csv`

1. `Read q1.csv and tell me the total of the Revenue ($) column.` (session 9304)  
   Expect: status success; message contains (4000|4,000)

### `CV-B05`: CSV operations (stress)

Header-only CSV: the total must be 0 (or a clear 'no rows' error), never invented.

Workspace: `empty.csv`

1. `Read empty.csv and tell me the total of the amount column.` (session 9305)  
   Expect: status either; message contains (re:\b0(\.0+)?\b|no rows|no data|empty|no values)

### `CV-B06`: CSV operations (grader-like)

Tab-separated file: update by ID, tabs preserved.

Workspace: `items.tsv`

1. `In items.tsv, change the status of the row with ID 2 to done, and save the result as out.tsv.` (session 9306)  
   Expect: status success; out.tsv bytes == b'id\tname\tstatus\n1\tpen\topen\n2\tink\tdone\n'

Allowed changes: `out.tsv`

### `CV-B07`: CSV operations (stress)

New value contains a double quote; the output must quote it correctly.

Workspace: `products.csv`

1. `In products.csv, change the note of the row with ID 2 to: 5" screen. Save the result as products_out.csv.` (session 9307)  
   Expect: status success; products_out.csv parses to 3 rows (header + 2)

Allowed changes: `products_out.csv`

### `CV-B08`: CSV operations (grader-like)

Implicit follow-up 'What about February?' after a January lookup.

Workspace: `monthly.csv`

1. `Read monthly.csv. What was the revenue in January?` (session 9308)  
   Expect: status success; message contains (120)
2. `What about February?` (session 9308)  
   Expect: status success; message contains (180); message does not contain any of ['re:\\b95\\b']

## CP3

### `PG-O`: Programming (grader-like)

The official prompt word for word (7-12 hidden); 40k rows with quoted commas, negatives, mixed-case categories, a category with '&' and a forced top-N tie. Graded on 1-6.

Prompt: the official `tally.py` task with requirements 7-12 hidden. Graded per requirement against `cp3/ref_tally.py` (see `cp3/prog_specs.py`).

### `PG-D`: Programming (grader-like)

Hidden-style 7-12 the agent was never tuned on: --json on every command (object shapes), count --invalid, monthly, stats with exit 1, usage errors with exit 2, exact half-up rounding of 3-decimal amounts. Data has CRLF, a repeated header, a blank line and 200 invalid rows.

Prompt: the official `tally.py` task with requirements 7-12 set to variant D. Graded per requirement against `cp3/ref_tally.py` (see `cp3/prog_specs.py`).

### `PG-E`: Programming (stress)

Hidden-style 7-12: --from/--to on every command, comma-separated category lists, bottom N, whitespace trimming, $/+ amount formats, --name=value options. Data has a UTF-8 BOM.

Prompt: the official `tally.py` task with requirements 7-12 set to variant E. Graded per requirement against `cp3/ref_tally.py` (see `cp3/prog_specs.py`).

### `DB-1`: Debugging (grader-like)

Misspelled culprit class (StockReconcilationJob) while the correct spelling appears in noise; earlier 409 attempt for the same order; older recovered stall on the same worker; look-alike decoy orders; four different log formats incl. log4j comma timestamps and JSON lines.

Logs: cp3/gen_incident.py, seed 12, services gateway/orders/inventory/payments, order ORD-77341. Graded per REPORT.json key (value and JSON type).

### `DB-2`: Debugging (stress)

Same traps with renamed service files (edge/checkout/stock/billing) and a different order id, so nothing about ORD-77341 or the default file names can be assumed.

Logs: cp3/gen_incident.py, seed 13, services edge/checkout/stock/billing, order ORD-58212. Graded per REPORT.json key (value and JSON type).

### `DB-3`: Debugging (grader-like)

Culprit SkuIndexReBuilder (capital B) vs SkuIndexRebuilder in noise.

Logs: cp3/gen_incident.py, seed 14, services gateway/orders/inventory/payments, order ORD-77341. Graded per REPORT.json key (value and JSON type).

### `DB-4`: Debugging (grader-like)

Baseline: the submission's own development generator with every scenario enabled (retry, decoy ids, decoy class, JSON lines, mixed timestamps).

Logs: submission's gen_logs.py (retry,decoy_ids,decoy_class,json_lines,mixed_ts), seed 9, services gateway/orders/inventory/payments, order ORD-77341. Graded per REPORT.json key (value and JSON type).

### `PG-F`: Programming (grader-like)

Hidden-style 7-12 with bigger outputs: monthly and monthly --json, median with an even/odd count, daily, export PATH (writes a CSV and prints 'wrote PATH'), and a 2-second speed limit on a 60,000-row file. This also checks the time budget when the self-review step runs.

Prompt: the official `tally.py` task with requirements 7-12 set to variant F. Graded per requirement against `cp3/ref_tally.py` (see `cp3/prog_specs.py`).

### `DB-B01`: Debugging (grader-like)

The cause is not a lock: the innermost 'Caused by' is java.util.concurrent.TimeoutException, whose first frame is java's FutureTask; the real component SupplierFeedClient is the next frame and has its own WARN line. The root-cause check may steer the model to the wrong class. Key 11 asks for the component's timeout instead of a lock name.

Logs: cp3/gen_incident.py, seed 21, services gateway/orders/inventory/payments, order ORD-77341. Graded per REPORT.json key (value and JSON type).

### `DB-B02`: Debugging (grader-like)

Different hidden keys: order_number (the string ORD-77341, but the '_number' suffix forces an integer), upstream_service, payment_cancel_event.

Logs: cp3/gen_incident.py, seed 22, services gateway/orders/inventory/payments, order ORD-77341. Graded per REPORT.json key (value and JSON type).

### `DB-B03`: Debugging (stress)

The gateway retried upstream: one req= has a first upstream_trace (connection reset, no hold) and a second one that waited on the hold and failed. The answer is the second trace.

Logs: cp3/gen_incident.py, seed 23, services gateway/orders/inventory/payments, order ORD-77341. Graded per REPORT.json key (value and JSON type).

