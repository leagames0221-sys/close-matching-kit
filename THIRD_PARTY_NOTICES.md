# Third-party notices

BlackLine is a product name of BlackLine, Inc. This repository has no relation to that company.

## Reconify (MIT)

Test fixtures under `tests/external/reconify/` are copied from https://github.com/reconifyhq/reconify.

```
MIT License

Copyright (c) 2024-present ReconifyHQ

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Splink datasets (MIT)

The test data (not stored in this repository; fetched into `var/`) comes from
https://github.com/moj-analytical-services/splink_datasets, Copyright (c) 2023 MoJ Analytical Services,
under the MIT License (same text as above with that copyright line).

## hyperswitch-reconciliation (Apache-2.0)

Only ideas were taken (no code, no NOTICE file exists). The Apache License 2.0 full text will be added
here if any file from that project is copied.

## OpenSSL (Apache-2.0)

Two lines of `providers/implementations/kdfs/scrypt.c` (branch openssl-3.0,
https://github.com/openssl/openssl) are quoted in `docs/sources.md` to show where the scrypt memory limit comes from.
No code is copied into the program. Copyright The OpenSSL Project Authors; Apache License 2.0,
https://www.apache.org/licenses/LICENSE-2.0.

## OWASP Cheat Sheet Series (CC BY-SA 4.0)

Short passages from the Cross-Site Request Forgery Prevention, Password Storage and Session Management Cheat Sheets
(https://cheatsheetseries.owasp.org/) are quoted or paraphrased in `docs/adr/` and `docs/sources.md`, with the source
named. License: Creative Commons Attribution-ShareAlike 4.0 International,
https://creativecommons.org/licenses/by-sa/4.0/. No change other than selection.

## Odoo documentation (CC BY-SA 4.0) and Odoo `account_lock` (LGPL-3)

Two sentences of "Reconciliation models" (https://github.com/odoo/documentation, branch 17.0,
`content/applications/finance/accounting/bank/reconciliation_models.rst`) are quoted in `docs/adr/0003`, `docs/adr/0006`
and `docs/sources.md`, with the source named. License: Creative Commons Attribution-ShareAlike 4.0 International,
https://creativecommons.org/licenses/by-sa/4.0/. No change other than selection. One sentence of the description of the
module `account_lock` (https://github.com/odoo/odoo, branch 17.0, `addons/account_lock/__manifest__.py`, LGPL-3) is
quoted in `docs/adr/0008` and `docs/sources.md`; only the idea is used, no code is copied.

## Frappe framework (MIT)

Two default values of `frappe/core/api/file.py` and one setting description of `system_settings.json`
(https://github.com/frappe/frappe, branch develop) are quoted in `close_kit/config.py`, `docs/adr/0004` and
`docs/sources.md`; the values are used as the file size limits. No code is copied. Copyright (c) 2016-2021 Frappe
Technologies Pvt. Ltd.; MIT License.

## Apache POI (Apache-2.0)

Two lines of `ZipSecureFile.java` and `ZipPackage.java` (https://github.com/apache/poi, branch trunk) are quoted in
`close_kit/config.py`, `docs/adr/0004`, `docs/sources.md` and a test; the value is used as the limit of entries in an
xlsx. No code is copied. Apache License 2.0, https://www.apache.org/licenses/LICENSE-2.0.

## Michael Nygard, "Documenting Architecture Decisions" (CC0)

The form of the files in `docs/adr/`. Dedicated to the public domain; no notice is required.

## National holidays CSV, Cabinet Office of Japan (Public Data License 1.0)

`close_kit/data/syukujitsu.csv` is the file https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv, unchanged.
出典：内閣府ウェブサイト（https://www8.cao.go.jp/chosei/shukujitsu/gaiyou.html）、公共データ利用規約（第1.0版）
（https://www.digital.go.jp/resources/open_data/public_data_license_v1.0）。 sha256 and size are in `docs/sources.md`.

## Internal control standard, Financial Services Agency of Japan (Public Data License 1.0)

Only ideas are taken (the person who approves is not the person who records; a change is approved before it is tested;
one sentence of the auditor's points on changes is quoted in `docs/adr/0008` and `docs/sources.md`). 出典：金融庁ウェブサイト
（https://www.fsa.go.jp/singi/singi_kigyou/kijun/20230407_naibutousei_kansa.pdf）、公共データ利用規約（第1.0版）。

## ZeraBooks month-end-checklists (CC BY 4.0)

The sample task list (`tools/make_samples.py`, `samples/sample_workbook.xlsx`) is a Japanese translation of six steps of
https://github.com/ZeraBooks/month-end-checklists (`small-business/close-tracker.csv`, commit
8d3170942651ecd039206d109c29950d72a0d27d), licensed under Creative Commons Attribution 4.0 International,
https://creativecommons.org/licenses/by/4.0/. Changes: selection of six steps, translation, due-day numbers read as
business days. ZeraBooks does not endorse this repository.

## Packages installed from PyPI (not copied into this repository)

See `docs/adr/0001-parts-and-licenses.md` for the 28 pinned packages. Two are not MIT / Apache-2.0 / BSD:
`typing-extensions` (PSF-2.0) and `certifi` (MPL-2.0, used unmodified). `pyarrow` (Apache-2.0) is used only in a
separate environment by `tools/convert_splink.py`.


## NIST SP 800-128 (United States government; no copyright in the United States)

The form of a change request (appendix E) and the steps of configuration change control (section 3.3) are used in
`docs/adr/0008-change-requests.md` and on the screens, in our own words. NIST asks for attribution: National Institute of
Standards and Technology, Special Publication 800-128, https://doi.org/10.6028/NIST.SP.800-128. The statement that the
publication is not subject to copyright is a statement about the United States.

## NIST SP 800-53 Rev. 5 (United States government; no copyright in the United States)

One sentence of control enhancement CM-4(1) is quoted in `docs/adr/0008-change-requests.md` and `docs/sources.md`.
Attribution: National Institute of Standards and Technology, Special Publication 800-53 Revision 5,
https://doi.org/10.6028/NIST.SP.800-53r5.

## Public Data License 1.0 (Financial Services Agency, Digital Agency)

Passages of the internal control standard (Financial Services Agency) and of the DS-120 practice guidebook (Digital Agency)
are summarised in `docs/adr/0008-change-requests.md` and `docs/deliverables/`. 出典：金融庁ウェブサイト
(https://www.fsa.go.jp/singi/singi_kigyou/kijun/20230407_naibutousei_kansa.pdf)、出典：デジタル庁
(https://www.digital.go.jp/assets/contents/node/basic_page/field_ref_resources/e2a06143-ed29-4f1d-9c31-0f06fca67afc/d85eeb55/20240605_resources_standard_guidelines_guideline_05.pdf).
The text is edited and summarised by this repository; it was not made by the Agency, and it is not presented as if it had been.

## CMS Technical Lifecycle Framework templates (the order of the headings only)

The order of the headings of the User Manual, Test Case Specification and Test Summary Report templates
(https://www.cms.gov/Research-Statistics-Data-and-Systems/CMS-Information-Technology/TLC) is followed in
`docs/deliverables/`. No sentence is copied. Whether the templates are free of copyright could not be confirmed, so only
the order of the headings is used.

## Diátaxis (CC BY-SA 4.0)

Only the idea that a how-to guide is a list of steps toward a goal is used (https://diataxis.fr/). No text is copied, so
the share-alike condition of the licence does not apply.
