# 0007. The period-end task list

Status: accepted (2026-10-24)

## Context

A month-end close is a list of steps with owners and due dates counted from the period end. The list should say who
prepares and who reviews each step, keep a step from being finished before the one it depends on, and - this is the
part that ties it to the rest of the tool - refuse to call a step done when the figures it is about do not hold.

## Decision

- **Made per period.** An administrator makes the tasks of a period. Recurring tasks are made for every period that is
  not closed, project and ad-hoc tasks only for their own period (BlackLine's task material names these three kinds and
  says recurring tasks are scheduled against the period end). The same task is never made twice for a period. A task
  keeps the definition it was made with (people, predecessors, due date rule, condition) - a newer workbook changes
  only the tasks made after it.
- **Due dates.** Calendar days: period end + N. Weekdays: Monday to Friday only; N = 0 is the period end
  itself, N > 0 the N-th weekday after it, N < 0 the |N|-th before it. Business days: like weekdays but national
  holidays and the company's own holidays are skipped. N is limited to a range that is a **setting of the workbook**: the
  two columns 「期日の日数の下限」 and 「期日の日数の上限」 of the 説明 sheet (0 must lie inside; Oracle's template always has
  Day Zero). Source (checked 2026-10-26, Oracle Hyperion Financial Close Management, "Setting Template Properties",
  https://docs.oracle.com/cd/E64585_01/FCLAD/cm_templates_properties.htm): *"You can specify the number of days a template
  tracks before and after the Zero day."* Oracle sets the range per template; here the workbook has one range for all
  its tasks. **Author's design decision:** the default when the columns are empty is -20..40; no model gives a number
  (Oracle's template pages, BlackLine's task material and OneStream's "Close Dates" page were read for one). The range
  only bounds what the workbook may ask for.
  None of the documents first read (BlackLine's task
  management sheet and product page; a BlackLine partner's slides and article, Sensiba; and the "Close Dates" page of
  another vendor, OneStream, https://documentation.onestream.com/1375907/Content/UTM/Close%20Dates.html) says whether
  day 0 or day 1 is the period end. The counting follows Oracle's Close Manager (checked 2026-10-26, "Creating Schedules
  from Templates", https://docs.oracle.com/cd/E64585_01/FCLAD/cm_templates_schedules_create.htm): tasks get dates *"based
  on their relationship to the template Day Zero"*, and *"if Day 0 is a Friday, the tasks for Day 1 are placed on the
  following Monday if you selected to exclude weekend days"*. Oracle lets the person choose the Day Zero date; here it is
  the period end (BlackLine: recurring tasks are scheduled against the period end). The sources say that due dates can use
  business days, calendar days or weekdays, a holiday calendar, and that a company adds its own holidays.
- **The holiday table** is the Cabinet Office's national holidays CSV, copied unchanged (Public Data License 1.0, credit in
  `docs/sources.md`), read as it is (cp932). Its range is 1 January of the first year to 31 December of the last year of
  the file (1955-01-01 to 2027-12-31 in the copy held here). A business-day count that needs a day outside it produces no
  due date and says 祝日表の範囲外; it never counts wrongly. The tests read the CSV themselves and write no holiday by hand.
- **Predecessors.** The list of predecessor ids is looked at **in the same period only**; an id that has no task in that
  period counts as "no predecessor" and a warning is given when the tasks are made. A task cannot become 作成完了 until all
  its predecessors of that period are 完了. This is the reading of *"Dependencies ensure that tasks are completed in the
  correct order"*; making a task impossible to start was considered and left out. **Author's design
  decision:** the limit to the same period. The closest source goes the other way: Oracle's Close Manager lets a task
  have *"predecessor tasks in other templates"* linked to tasks of existing schedules; this tool does not build that (to
  keep the task list small). A cycle is refused when the workbook is
  read.
- **Preparer and reviewer** are different people (checked when the workbook is read and again on the operation): 未着手 ->
  作成完了 by the preparer named in the task, -> 完了 by the reviewer named in the task; the reviewer can send it back with a
  reason. Each event is kept with the person and the time (BlackLine: *"Full audit trails track and timestamp all
  certification events"*). The idea of a preparer and a reviewer who are not the same comes from a university finance
  office's month-end guidance (a checklist prepared by one person is reviewed and approved by someone else) and from
  BlackLine's buyer's guide (segregation of duties). **Author's design decision:** applying it to each task (the Cambridge
  guidance speaks of a checklist, not of each task on it).
- **Completion conditions (author's design decision).** No model ties a task to matching. The closest source is
  BlackLine's *"If your rec has been completed and the balance changes, BlackLine decertifies the rec and alerts users via
  email."* (Account Reconciliations): a completion that depends on the reconciliation's figures. The conditions use those
  figures:
  `なし`; `未処理 0 件` (for a match set and a period, as of the period end: no unmatched, duplicate or candidate row
  without an approved adjusting item or a journal; no manual match waiting; no group waiting for its difference or for a
  journal - counted in the period of its latest bank row, the date its journal takes, so a pair paid after the period
  end with a difference holds up the next period, not this one; no adjusting item waiting); `残高一致の差 0` (the statement of ADR 0006 for that period has a known balance and a
  difference of 0). The condition is checked at 作成完了 and at 完了, and a refusal gives the reasons.
- **Undoing a completion.** After an import, a match run, or the approval, rejection or withdrawal of a journal or an
  adjusting item, every 完了 task with a condition is checked again; one that no longer holds goes back to 作成完了, the
  reason is kept in the task, in the task's event list and in the operation log. BlackLine's account reconciliation
  material says a completed reconciliation is decertified when the balance changes. A period's figures are as of its
  period end, so events after it do not undo the tasks of that period.
- **The sample list** takes six of the thirteen steps of a CC BY 4.0 month-end close tracker (ZeraBooks, commit
  8d31709), translated into Japanese; its "Due day: Day N" is read as the N-th business day after the period end, because the README of the same folder
  at that commit (`small-business/README.md`) says: *"Due day counts working days after month end."*

## Alternatives considered

- **Making a task impossible to start before its predecessors are done.** Considered and left out (Predecessors,
  above). BlackLine's task sheet says that dependencies ensure tasks are completed in the correct order; only 作成完了 is
  blocked.
- **Predecessors in other periods or other templates.** Oracle's Close Manager allows predecessor tasks in other
  templates. Not built, to keep the task list small (Predecessors, above).
- **A range of days for each template.** Oracle sets the range per template. Here one range serves the whole workbook;
  before 2026-10-26 it was fixed at -20 to 40 (ADR 0003, revision table).
- **The period end as day 1.** None of the documents first read says whether the period end is day 0 or day 1. The
  counting follows Oracle's Close Manager (Due dates, above).
- **"Day N" of the sample list as calendar days.** Read as business days, because the README next to the tracker says that
  the due day counts working days after month end.
- **The University of Cambridge's month-end checklists as the sample list.** Looked at; only the idea of a reviewer who is
  not the preparer is used. The page carries a copyright notice of the University and no reuse terms were found, so none
  of its text is used (`docs/sources.md`). The sample list comes from a CC BY 4.0 tracker.
- **A reviewer for the whole checklist.** The Cambridge guidance speaks of a checklist; the reviewer is set for each task
  (author's decision above).
- **No other option was compared** for the holiday table (the Cabinet Office's national holidays CSV is the only one the
  records name).

## Consequences

- "Done" on a reconciliation task means the statement was zero when somebody looked, and the list notices when that
  stops being true.
- The counting rule and the conditions are small, explicit and tested; a reader who disagrees with day 0 changes one
  function.
- Time of day and time zones are not modelled; due dates are dates.
