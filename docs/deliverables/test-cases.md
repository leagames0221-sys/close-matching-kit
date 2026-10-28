# テストケース仕様

様式の出典：CMS Technical Lifecycle Framework "Test Case Specification"（https://www.cms.gov/Research-Statistics-Data-and-Systems/CMS-Information-Technology/TLC/Downloads/Test-Case-Specification-Document.docx ）の章立て（概要・一覧・要件との対応表・各ケースの目的・依存・対象・前提・入力・期待結果・合否の基準・手順・仮定と制約）。章立てだけを使い、文は自作した。この repo に合わせて章を変えた（変更：先頭に要件の一覧を置き、各ケースに、確かめる pytest の試験の名前を持たせた）。

## 1. 概要

### 1.1 この文書
照合キットの受け入れの確かめを、番号つきのテストケースに分けた物。各ケースは、それを確かめる pytest の試験（`tests/test_x.py::test_name`）の名前を持つ。`test-summary-report.md` は、この文書の名前を使って、pytest の結果から作る（`tools/test_report.py`）。**ケースは、その名前の試験が全部合格した時だけ「合格」**になる。名前が結果に見つからない時は、報告を作らずに止まる。

ブラウザで人が通すケースは、「人が通すケース：はい」と書いてある。それは自動では合格にならず、報告では「保留」になる。通した日時・誰が・画面の画像を、そのケースの「実施の記録」に書く。

### 1.2 要件の一覧
受け入れの 13 項目（見る人が、手順の中で見る物）を、自分の文で並べた。以下、R-01 から R-13 と呼ぶ。

| 番号 | 要件 |
|---|---|
| R-01 | 見本のワークブックを読み込むと、照合セット・パスルール・承認ルート・作業一覧が画面に出る。わざと誤りを入れたワークブックは、シート名と行を示して拒否され、何も保存されない |
| R-02 | 残高が合わない銀行明細と、すでに取り込んだ範囲と重なる同じ明細の 2 回目は、取り込みを拒否される |
| R-03 | 正解付きの試験データで、自動・候補・未照合・重複の件数と、区分ごとの適合率・再現率が計算できる |
| R-04 | 1 対 1・多対 1・多対多の照合が、期待した組で当たる |
| R-05 | 候補を手で照合しても、別の人が承認するまで「完了」にならない。照合した本人は承認できない |
| R-06 | 金額の差のある照合に理由コードを付けると、「仕訳」の理由なら差額の仕訳が、「調整項目」の理由なら調整項目ができる。規則に差額の相手勘定があれば、許容差の中の差額の仕訳は道具が下書きに作る |
| R-07 | 借方と貸方が合わない仕訳、勘定表に無い科目の仕訳、締めた期間の仕訳は、保存できない |
| R-08 | 金額の閾値のすぐ下とすぐ上で承認者が変わる。作成者が自分の仕訳を承認しようとすると、サーバーが拒否する |
| R-09 | 期末の残高一致表で、調整後の銀行残高と調整後の帳簿残高の差が計算され、差があるとその作業が完了にならない |
| R-10 | 作業一覧は前の作業が終わるまで次を進められず、期日が営業日・暦日で出る。監査人は見るだけで、変更の操作はサーバーが拒否する。操作の記録に誰が・いつ・何を・どの版でが出て、CSV で出せる |
| R-11 | 設定を変えると、変更の申請 → 事前承認 → 試験の結果（前後の件数・区分が変わった行）→ 結果の承認（2 人）→ 本番に適用 → 確認が残る。申請者は、どの承認もできない。前の版に戻すのも同じ流れで、戻した版に今の締めが入る。本番と sandbox は URL と送信の形式の両方で区別され、転記ファイルは本番だけが出す |
| R-12 | 各テストケースの番号に、検証結果の報告の 1 行が 1 対 1 で結び付き、報告の数は pytest の出力から作られる |
| R-13 | QA 台帳の 6 つの質問に、調べた事・対応の可否・代替案・出典が書いてある |

### 1.3 試験の環境
Windows、Python 3.x、`.venv\Scripts\python -m pytest`。外への通信はしない。データベースは試験ごとの使い捨てのファイル。見本のファイルは git にある `samples/`。Splink の試験データ（git に入っていない）は pytest では使わない（`docs/validation/results.md` に別に記録）。

## 2. テストケースの一覧

| 番号 | ケース | 要件 | 種類 |
|---|---|---|---|
| TC-01 | 設定のワークブックの読み込みと拒否 | R-01 | pytest |
| TC-02 | 取り込みの検算と重複の拒否 | R-02 | pytest |
| TC-03 | 照合の数え方（件数・適合率・再現率） | R-03 | pytest |
| TC-04 | 1 対 1・多対 1・多対多の照合 | R-04 | pytest |
| TC-05 | 手の照合と別の人の承認 | R-05 | pytest |
| TC-06 | 理由コードから仕訳・調整項目 | R-06 | pytest |
| TC-07 | 仕訳の検証 | R-07 | pytest |
| TC-08 | 承認ルートと本人の承認の拒否 | R-08 | pytest |
| TC-09 | 期末の残高一致表 | R-09 | pytest |
| TC-10 | 作業一覧・監査人・操作の記録 | R-10 | pytest |
| TC-11 | 変更の申請の流れと人の決まり | R-11 | pytest |
| TC-12 | 変更の試験の中身 | R-11 | pytest |
| TC-13 | 同時の申請・締め・戻し・記録 | R-11 | pytest |
| TC-14 | 環境の区別と本番の画面・転記ファイル | R-11 | pytest |
| TC-15 | 検証結果の報告を作る道具 | R-12 | pytest |
| TC-16 | QA 台帳 | R-13 | pytest |
| TC-17 | 利用者の認証と要求の確かめ | R-10 | pytest |
| TC-18 | 承認者が画面で仕訳を承認する | R-08 | 人がブラウザで通す |
| TC-19 | 本番で見本の道筋を流す | R-09, R-11 | 人がブラウザで通す |
| TC-20 | 変更の申請を画面で通す | R-11 | 人がブラウザで通す |

## 3. 要件との対応表

| 要件 | ケース |
|---|---|
| R-01 | TC-01 |
| R-02 | TC-02 |
| R-03 | TC-03 |
| R-04 | TC-04 |
| R-05 | TC-05 |
| R-06 | TC-06 |
| R-07 | TC-07 |
| R-08 | TC-08, TC-18 |
| R-09 | TC-09, TC-19 |
| R-10 | TC-10, TC-17 |
| R-11 | TC-11, TC-12, TC-13, TC-14, TC-19, TC-20 |
| R-12 | TC-15 |
| R-13 | TC-16 |

## 4. テストケースの詳細

### TC-01 設定のワークブックの読み込みと拒否
- 要件：R-01
- 目的：見本のワークブックが読めて、誤りのあるワークブックが、場所を示して全部拒否されることを確かめる。
- 依存：なし。
- 対象：`close_kit/workbook.py`、`close_kit/versions.py`、設定の画面。
- 前提：`samples/sample_workbook.xlsx` と、わざと誤りを入れたワークブックの組み立て（`tools/make_samples.py`）。
- 入力：見本のワークブック。シート名・列・型・規則の種類・比較演算子・数式・巨大な zip・DOCTYPE を壊した物。
- 期待結果：見本は同じ中身に正規化されて読める。壊した物は、シート名・行・列つきの一覧で拒否され、何も保存されない。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_workbook_versions.py::test_good_workbook_reads_and_is_canonical`、`tests/test_workbook_versions.py::test_errors_carry_sheet_row_column_and_nothing_accepted`、`tests/test_workbook_versions.py::test_missing_column_and_wrong_type`、`tests/test_workbook_versions.py::test_nothing_saved_when_refused`、`tests/test_workbook_versions.py::test_xlsm_formula_and_big_zip_refused`、`tests/test_workbook_versions.py::test_xlsx_part_with_doctype_or_entity_is_refused_before_parsing`、`tests/test_close_foundation.py::test_the_closing_sample_workbook_reads_and_has_the_new_content`、`tests/test_close_foundation.py::test_a_version_1_workbook_is_refused_as_too_old`、`tests/test_web.py::test_acceptance_flow_over_http` を流す。
- 仮定と制約：画面の見た目は人がブラウザで見る（TC-18〜20）。

### TC-02 取り込みの検算と重複の拒否
- 要件：R-02
- 目的：残高が合わない明細、同じ範囲の 2 回目、同じ内容の CSV が、ファイルごと拒否されることを確かめる。
- 依存：TC-01。
- 対象：`close_kit/ingest.py`、`close_kit/formats/`。
- 前提：設定の版が入っている。
- 入力：全銀（残高の合わない物、構造の誤り）、camt.052（残高 0 で明細あり、件数の不一致）、CSV（残高列の食い違い）。同じファイルの 2 回目。
- 期待結果：理由つきで拒否され、行は 1 行も保存されない。2 回目は範囲の重なりで拒否される。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_formats_ingest.py::test_ingest_refuses_unbalanced_zengin_and_stores_nothing`、`tests/test_formats_ingest.py::test_second_import_of_same_range_refused`、`tests/test_formats_ingest.py::test_csv_duplicate_by_sha256_and_by_row_key`、`tests/test_formats_ingest.py::test_zengin_balance_trailer_and_end_mismatches_name_the_figure`、`tests/test_formats_ingest.py::test_camt_balance_and_count_mismatch`、`tests/test_formats_ingest.py::test_ingest_is_checked_by_role_on_the_server`、`tests/test_close_foundation.py::test_csv_balance_mismatch_inside_the_file_is_refused` を流す。
- 仮定と制約：EBCDIC の全銀と PDF の明細は読まない。

### TC-03 照合の数え方（件数・適合率・再現率）
- 要件：R-03
- 目的：区分ごとの件数と、適合率・再現率の式が、手計算と合うことを確かめる。
- 依存：TC-04。
- 対象：`close_kit/metrics.py`、`close_kit/matching.py`。
- 前提：Reconify の評価データ（`tests/external/reconify/`）と見本。
- 入力：正解の組と、照合の結果。
- 期待結果：手計算と同じ率。分母が 0 の時は「該当なし」。評価データの組が再現される。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_metrics.py::test_formulas_match_a_hand_calculation`、`tests/test_metrics.py::test_zero_denominators_are_not_applicable`、`tests/test_metrics.py::test_groups_expand_to_every_left_right_pair`、`tests/test_metrics.py::test_from_database_uses_automatic_groups_only_for_precision`、`tests/test_reconify_evals.py::test_expected_pairs_are_reproduced`、`tests/test_reconify_evals.py::test_bundle_evals_give_the_expected_groups_and_only_the_duplicate_mark_differs`、`tests/test_reconify_evals.py::test_003_same_categories_and_the_difference_within_tolerance_is_done_with_its_journal_drafted`、`tests/test_reconify_evals.py::test_002_duplicates_are_matched_with_a_mark_and_two_possible_partners_make_a_candidate`、`tests/test_reconify_evals.py::test_every_fetched_eval_is_covered` を流す。
- 仮定と制約：Splink の金融取引の例での率（適合率 0.9959、再現率 0.2470。ルールの変更の後にもう一度流して、同じ数だった）は、データが git に入っていないため pytest では流さず、`docs/validation/results.md` に別に記録してある。再現率が低いのは、等しいか否かのルールだけだから（同じ文書に理由がある）。

### TC-04 1 対 1・多対 1・多対多の照合
- 要件：R-04
- 目的：見本の ID で、照合の形ごとに期待した組が当たることを確かめる。
- 依存：なし。
- 対象：`close_kit/matching.py`、`close_kit/matchrun.py`。
- 前提：見本のデータ（`tools/sample_builders.py`）。
- 入力：参照・金額・日付の組み合わせを変えた行。
- 期待結果：期待した区分（自動・候補・未照合・重複）と状態になる。境目（金額の許容差、日付の幅）は含む。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_matching.py::test_row1_auto_within_window_zero_difference_is_done_and_leaves_the_remainder`、`tests/test_matching.py::test_row2_auto_within_tolerance_but_nonzero_difference_waits_for_the_difference_when_the_rule_has_no_account`、`tests/test_matching.py::test_row2_auto_within_tolerance_with_an_account_for_the_difference_is_done_and_names_the_account`、`tests/test_matching.py::test_row5_two_possible_partners_is_never_automatic`、`tests/test_matching.py::test_many_to_one_one_to_many_and_many_to_many_totals`、`tests/test_matching.py::test_bundle_with_different_total_is_a_candidate_not_automatic`、`tests/test_matching.py::test_amount_tolerance_boundary_is_inclusive`、`tests/test_matching.py::test_date_window_boundary_is_inclusive`、`tests/test_matching.py::test_duplicate_rows_take_part_in_matching_and_two_possible_partners_make_a_candidate`、`tests/test_matching.py::test_a_twin_far_away_in_date_still_counts_as_a_possible_partner_through_the_shared_reference`、`tests/test_matching.py::test_duplicate_rows_without_a_partner_stay_in_the_class_duplicate`、`tests/test_matching.py::test_duplicates_on_both_sides_make_one_candidate_of_all_their_rows`、`tests/test_matching.py::test_a_later_automatic_rule_does_not_use_a_row_of_an_earlier_candidate`、`tests/test_matching.py::test_a_later_automatic_rule_still_takes_rows_that_no_candidate_holds`、`tests/test_matching.py::test_bundles_use_no_amount_tolerance_and_compare_dates_only_when_the_date_is_a_bundle_key`、`tests/test_matching.py::test_an_automatic_bundle_with_a_total_off_by_one_is_a_candidate_even_inside_the_rules_tolerance`、`tests/test_matching.py::test_the_duplicate_mark_is_shown_for_rows_that_took_part_in_matching`、`tests/test_matching.py::test_sample_dataset_gives_the_expected_classes`、`tests/test_matching.py::test_rerun_keeps_fixed_groups_and_their_ids_and_recomputes_the_rest` を流す。
- 仮定と制約：金額の比べは等しいか許容差の中かだけ（点数で決める照合は作らない）。束ねる規則は金額の許容差を使わない（Oracle）。1 つの規則の候補の行は、後の規則が使わない（作者の判断。Odoo は「複数のモデルに当たる時は順番の先のモデルを使う」と書くだけで、候補の行を後の規則から外すとは書かない）ので、見本のワークブックでは、1 つの請求書の 3 回の分割入金が参照の規則の候補（ambiguous）になり、後ろの束ねる規則（40）は当たらない。

### TC-05 手の照合と別の人の承認
- 要件：R-05
- 目的：手の照合が、別の人の承認まで完了にならず、本人は承認できないことを確かめる。
- 依存：TC-04。
- 対象：`close_kit/manual.py`、手の照合の画面。
- 前提：候補の行がある。
- 入力：作成者が組にして、承認者が承認・却下する。作成者と承認者の両方の役割を持つ人が自分の組を承認しようとする。
- 期待結果：作成直後は「承認待ち」。別の承認者が承認すると完了（合計が合わなければ差額の処理待ち）。本人の承認は拒否される。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_manual_roles.py::test_manual_match_waits_for_another_persons_approval`、`tests/test_manual_roles.py::test_approval_gives_done_when_the_sides_add_up`、`tests/test_manual_roles.py::test_self_approval_is_refused_even_with_both_roles`、`tests/test_manual_roles.py::test_reject_returns_the_rows_to_candidates`、`tests/test_web.py::test_manual_match_and_approval_over_http`、`tests/test_web.py::test_self_approval_with_both_roles_over_http` を流す。
- 仮定と制約：BlackLine の資料では手の照合の承認は任意。この道具は必須（`qa-log.md` Q-06）。

### TC-06 理由コードから仕訳・調整項目
- 要件：R-06
- 目的：差額のある照合に理由コードを付けると、理由の処理に応じて仕訳か調整項目ができることを確かめる。
- 依存：TC-04。
- 対象：`close_kit/reasons.py`、`close_kit/journals.py`。
- 前提：見本の道筋（手数料の差、受取利息、月末の入金）。
- 入力：理由コード R01（仕訳）、R03（仕訳）、R04（調整項目）。規則に「差額の相手勘定」がある版と無い版。
- 期待結果：差額の方向どおりの借方・貸方の仕訳の下書き。規則に「差額の相手勘定」があれば、許容差の中の差額の組は完了になり、道具（利用者「照合の道具」）が下書きを作る。それを役割「作成者」の誰かが申請し、承認者が承認する（作った人は道具なので、承認者は必ず作った人と別）。調整項目は別の人の承認が要る。重複・候補には付けられない。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_tool_drafts.py::test_the_pair_is_done_at_once_and_the_tool_drafts_the_journal_of_its_difference`、`tests/test_tool_drafts.py::test_the_maker_is_the_tool_and_nobody_can_log_in_as_it`、`tests/test_tool_drafts.py::test_a_second_run_does_not_draft_the_journal_again`、`tests/test_tool_drafts.py::test_a_person_with_the_role_maker_submits_it_and_an_approver_decides_it`、`tests/test_tool_drafts.py::test_every_approver_is_a_person_other_than_the_maker_even_the_one_who_submitted`、`tests/test_tool_drafts.py::test_the_sample_path_with_the_tool_drafted_fee_journal_reaches_a_difference_of_zero`、`tests/test_tool_drafts.py::test_while_the_draft_waits_the_statement_lists_the_difference_and_the_condition_counts_it`、`tests/test_tool_drafts.py::test_a_rejected_or_withdrawn_draft_gives_the_pair_back_to_the_reason_code`、`tests/test_tool_drafts.py::test_a_draft_can_be_withdrawn_by_any_maker_and_the_pair_waits_again`、`tests/test_tool_drafts.py::test_a_person_made_journal_still_can_be_submitted_only_by_its_maker`、`tests/test_tool_drafts.py::test_a_pair_inside_the_tolerance_of_a_rule_without_an_account_waits_as_before`、`tests/test_tool_drafts.py::test_when_no_journal_can_be_drafted_the_pair_waits_for_a_person_and_the_run_says_why`、`tests/test_tool_drafts.py::test_the_rule_with_an_account_is_checked_when_the_workbook_is_loaded`、`tests/test_tool_drafts.py::test_a_workbook_made_before_the_column_existed_is_still_read`、`tests/test_tool_drafts.py::test_the_tool_does_not_change_the_group_ids_or_the_rows_of_a_fixed_pair`、`tests/test_close_screens.py::test_the_screens_show_the_duplicate_mark_and_the_run_message_names_the_drafts`、`tests/test_close_foundation.py::test_migration_4_adds_the_value_for_a_journal_the_tool_drafted_and_keeps_rows_triggers_and_links`、`tests/test_close_journals.py::test_difference_group_with_a_journal_reason_makes_the_fee_journal`、`tests/test_close_journals.py::test_the_direction_of_the_journal_follows_bank_minus_book`、`tests/test_close_journals.py::test_what_a_reason_code_may_be_attached_to`、`tests/test_close_journals.py::test_duplicates_and_candidates_take_no_reason_code`、`tests/test_close_journals.py::test_an_adjusting_item_needs_another_persons_approval`、`tests/test_close_journals.py::test_approval_completes_the_group_and_marks_the_row_rejection_and_withdrawal_undo_it` を流す。
- 仮定と制約：外貨と複数の会社コードは作っていない。「差額の相手勘定」が無い規則では、許容差の中の差額の組は差額の処理待ちのままで、人が理由コードを付ける（手本の Odoo・Oracle は相手勘定が無い時を書いていない。`docs/sources.md`）。

### TC-07 仕訳の検証
- 要件：R-07
- 目的：誤りのある仕訳が、保存・申請・承認のどの時点でも通らないことを確かめる。
- 依存：なし。
- 対象：`close_kit/journals.py`。
- 前提：見本の会計期間（2026-09 は締めてある）と勘定表。
- 入力：借方と貸方が 1 違う仕訳、明細の行数の境目、勘定表に無いコード、締めた期間の日付、数式の文字で始まる摘要、誤りを含む CSV。
- 期待結果：規則の番号・行・値つきで拒否され、保存されない。CSV は 1 件でも誤りがあれば全体を拒否する。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_close_journals.py::test_rule1_debit_and_credit_one_unit_apart_are_refused`、`tests/test_close_journals.py::test_rule2_line_count_boundaries`、`tests/test_close_journals.py::test_rule3_unknown_account_code_names_the_line_and_the_value`、`tests/test_close_journals.py::test_rule4_closed_first_and_last_day_outside_every_period_and_not_a_date`、`tests/test_close_journals.py::test_rule5_a_memo_that_would_be_a_formula_is_refused`、`tests/test_close_journals.py::test_save_refuses_and_stores_nothing`、`tests/test_close_journals.py::test_one_error_stops_the_whole_import_and_names_the_rows`、`tests/test_close_journals.py::test_period_closed_between_submission_and_approval_stops_the_approval` を流す。
- 仮定と制約：検証は保存・申請・承認の 3 回かかる。

### TC-08 承認ルートと本人の承認の拒否
- 要件：R-08
- 目的：金額の閾値の上下で承認できる人が変わり、作成者自身の承認が拒否されることを確かめる。
- 依存：TC-07。
- 対象：`close_kit/journals.py`、`close_kit/roles.py`。
- 前提：承認ルート（0 以上 10 万円未満、10 万円以上）。
- 入力：99,999 円と 100,000 円の仕訳。作成者本人、役割だけ持つ承認者、名前のある承認者。
- 期待結果：幅の中に名前のある承認者だけが決められる。作成者本人は、役割をいくつ持っていても拒否される。拒否は操作の記録に残る。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_close_journals.py::test_route_for_the_amount_just_below_and_at_the_limit`、`tests/test_close_journals.py::test_the_person_who_may_approve_changes_at_the_limit`、`tests/test_close_journals.py::test_a_name_in_the_route_without_the_approver_role_is_refused`、`tests/test_close_journals.py::test_the_approver_role_without_a_name_in_the_range_is_refused`、`tests/test_close_journals.py::test_the_author_cannot_decide_even_with_both_roles_and_a_name_in_the_route`、`tests/test_close_journals.py::test_the_approval_record_names_who_when_and_which_version`、`tests/test_close_journals.py::test_the_database_refuses_to_change_or_remove_an_approved_journal`、`tests/test_close_roles_web.py::test_the_month_end_story_over_http`、`tests/test_changes_web.py::test_the_approvers_how_to_in_the_manual_names_what_the_screens_show` を流す。
- 仮定と制約：承認は 1 段（金額の幅ごとの承認できる人）。

### TC-09 期末の残高一致表
- 要件：R-09
- 目的：調整後の銀行残高と調整後の帳簿残高の差が正しく出て、差があると作業が完了にならないことを確かめる。
- 依存：TC-06。
- 対象：`close_kit/balance.py`、`close_kit/tasks.py`。
- 前提：見本の道筋。様式の見本（Department of Justice の様式）の数字。
- 入力：様式の見本の数字、見本の道筋の 4 つの手順、承認前・却下後の調整項目。
- 期待結果：様式の見本は計算値（98,860）で合う。見本の道筋で 2026-10 と 2026-11 の差が 0。承認前の調整項目は表に入らない。差がある間は条件つきの作業が完了にならない。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_close_statement.py::test_the_ojp_example_computed_from_its_own_figures`、`tests/test_close_statement.py::test_zero_difference_when_the_books_side_really_is_99000`、`tests/test_close_statement.py::test_the_four_steps_of_the_sample_give_a_difference_of_zero`、`tests/test_close_statement.py::test_an_item_is_not_in_the_statement_before_it_is_approved`、`tests/test_close_statement.py::test_rejecting_one_item_leaves_its_amount_as_the_difference`、`tests/test_close_statement.py::test_events_after_the_period_end_do_not_change_that_periods_statement`、`tests/test_close_statement.py::test_the_walk_of_the_sample_reproduces_the_path_to_zero`、`tests/test_close_tasks.py::test_the_balance_condition_blocks_until_the_difference_is_zero` を流す。
- 仮定と制約：表は期末の時点の見え方。期末をまたぐ組は差に残す。

### TC-10 作業一覧・監査人・操作の記録
- 要件：R-10
- 目的：作業の順序と期日、監査人の読み取り専用、操作の記録を確かめる。
- 依存：TC-09。
- 対象：`close_kit/tasks.py`、`close_kit/holidays.py`、`close_kit/audit.py`、`close_kit/roles.py`。
- 前提：内閣府の祝日表（変えずに入れてある）と会社の休日。
- 入力：営業日・暦日・平日の期日、前の作業が未完の作業、監査人の操作、役割ごとの操作。
- 期待結果：前の作業が完了するまで作成完了にできない。期日は期末日からの日数で出て、祝日を飛ばす。監査人の変更の操作は 403。操作の記録は書き換えも削除もできず、CSV で出る。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_close_tasks.py::test_a_task_cannot_be_prepared_before_its_predecessor_is_complete`、`tests/test_close_tasks.py::test_business_days_skip_the_national_holiday`、`tests/test_close_tasks.py::test_company_holidays_are_skipped_too`、`tests/test_close_tasks.py::test_calendar_days`、`tests/test_close_foundation.py::test_the_range_of_days_from_the_period_end_is_a_setting_of_the_workbook`、`tests/test_close_tasks.py::test_only_the_named_preparer_and_reviewer_may_act`、`tests/test_close_roles_web.py::test_the_auditor_sees_every_screen_and_changes_nothing`、`tests/test_close_roles_web.py::test_direct_post_without_the_role_is_refused_on_the_server`、`tests/test_close_roles_web.py::test_every_operation_by_every_role_is_checked_on_the_server`、`tests/test_foundation.py::test_audit_table_is_append_only`、`tests/test_foundation.py::test_audit_csv_neutralises_formula_cells`、`tests/test_web.py::test_audit_log_page_and_csv` を流す。
- 仮定と制約：「0 日目」の数え方は、Oracle の決算の作業管理（Close Manager）の Day Zero に合わせた（`qa-log.md` Q-04）。0 日目を期末日に置くのは BlackLine の作業管理の資料の「Automatically schedule recurring tasks in relation to your period-end date」による（0 日目か 1 日目かは書いていない）。日数の範囲は Oracle の「テンプレートが Zero day の前後に追う日数を指定できる」に合わせてワークブックの設定にした（空なら -20〜40。この数は作者の設計判断）。

### TC-11 変更の申請の流れと人の決まり
- 要件：R-11
- 目的：変更の申請が定めた順に進み、申請者がどの承認もできず、結果の承認が 2 人（承認者と管理者）で通ることを確かめる。
- 依存：TC-01、TC-12。
- 対象：`close_kit/changes.py`、`close_kit/web_changes.py`、変更の申請の画面。
- 前提：sandbox に読み込んだ版。管理者 2 人、承認者 2 人、監査人。
- 入力：申請・事前承認・結果の承認・適用・確認の操作を、申請者・申請者以外・役割の無い人が、画面と直接の送信の両方で行う。
- 期待結果：状態が 下書き → 事前承認待ち → 試験待ち → 試験済み → 結果の承認済み → 本番に適用済み → 確認済み と進む。申請者は役割をいくつ持っていても、事前承認・却下・結果の承認・却下・適用が 403。結果の承認は承認者の役割の人 1 人と管理者の役割の人 1 人がそろうまで進まない（同じ人が両方持っても 1 人分）。試験をやり直すと前の承認は無効で、画面を開いた後に試験が流し直されると、その画面からの承認は拒否される（承認は画面に出ていた試験の結果に結ぶ）。拒否は、状態・空の欄・二度目の承認の物も含めて、1 回ずつ操作の記録に残る。確認できないは閉じない。取り下げは適用の前まで。表の「○ の無い組」は全部拒否される。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_changes.py::test_the_first_request_goes_through_every_step_and_leaves_who_when_in_which_role`、`tests/test_changes.py::test_what_the_form_fills_by_itself_is_fixed_when_it_is_put_forward`、`tests/test_changes.py::test_the_requester_cannot_approve_reject_or_apply_whatever_roles_he_has`、`tests/test_changes.py::test_the_pre_approval_is_for_an_administrator_who_is_not_the_requester`、`tests/test_changes.py::test_the_result_needs_an_approver_and_an_administrator_who_are_two_people`、`tests/test_changes.py::test_two_administrators_without_an_approver_are_not_enough`、`tests/test_changes.py::test_one_person_with_both_roles_counts_once`、`tests/test_changes.py::test_a_new_test_voids_the_approvals_given_to_the_earlier_result`、`tests/test_changes.py::test_either_kind_of_person_can_reject_the_result_and_the_request_is_closed`、`tests/test_changes.py::test_a_request_can_be_withdrawn_by_its_requester_until_it_is_applied`、`tests/test_changes.py::test_a_change_that_cannot_be_confirmed_stays_open_and_a_rollback_draft_can_be_made`、`tests/test_changes_web.py::test_the_whole_way_on_the_screens_and_the_requester_is_refused_at_every_step`、`tests/test_changes_web.py::test_every_empty_cell_of_the_table_is_refused_on_a_direct_post`、`tests/test_changes_web.py::test_only_an_administrator_makes_a_request_and_only_administrators_and_auditors_read_the_csv`、`tests/test_close_roles_web.py::test_the_table_of_the_second_release_is_exactly_this_and_the_first_one_is_untouched`、`tests/test_changes.py::test_an_approval_names_the_result_it_was_given_for_and_an_earlier_one_is_refused`、`tests/test_changes.py::test_every_refused_step_of_a_request_is_a_row_of_the_operation_log_once`、`tests/test_changes_web.py::test_a_page_opened_before_the_test_ran_again_cannot_approve_the_new_result`、`tests/test_changes_web.py::test_a_number_field_that_is_not_a_number_is_refused_not_an_error` を流す。
- 仮定と制約：「承認者と管理者の 2 人」は、金融庁の「利用する部門の適切な管理者及びＩＴ部門の適切な管理者」を、この道具の役割に読み替えた物（読み替えは作者の設計判断。手本の文は部門を前提にしている。`docs/adr/0008`）。確認を申請者がするのも作者の設計判断（NIST SP 800-128 の手順 viii は誰が確かめるかを書いていない）。

### TC-12 変更の試験の中身
- 要件：R-11
- 目的：事前承認の後に自動で流れる試験が、使い捨てのデータベースの上で、本番の写しの前と後・見本の道筋・変わったシートの効き目を出し、本番と sandbox を変えないことを確かめる。
- 依存：TC-04、TC-09。
- 対象：`close_kit/changecheck.py`、`close_kit/changeinfo.py`。
- 前提：本番の版と、本番に取り込んだ見本の明細。
- 入力：パスルールの許容差を変えた版、変えない版、承認ルートの閾値を変えた版、作業の期日を変えた版、見本のデータ源と形の違う版、承認者が外れて見本の道筋が止まる版。
- 期待結果：許容差を変えた版では、本番の写しで区分の件数が前と後で変わり、区分が変わった行が並ぶ。変えない版では変わらない。承認ルートは各幅の境目の金額で承認できる人の前と後が並び、本番の承認待ちの仕訳の分も出る。変更のせいで止まった時は「失敗（変更の影響）」として記録し、結果の承認に進む。道具の不具合は結果にならない。試験の前と後で、sandbox と本番のどの表の行も同じ。パスワードの強さなどの全体の値を変えない。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_changes_check.py::test_a_pass_rule_change_moves_rows_between_classes_in_a_copy_of_production`、`tests/test_changes_check.py::test_a_group_that_is_settled_in_production_is_not_reclassified_by_the_test_as_a_match_run_would_not`、`tests/test_changes_check.py::test_a_change_that_touches_nothing_in_the_data_leaves_every_class_as_it_was`、`tests/test_changes_check.py::test_with_no_data_in_production_the_result_says_so`、`tests/test_changes_check.py::test_the_first_request_runs_the_sample_path_only`、`tests/test_changes_check.py::test_a_version_whose_data_sources_differ_from_the_sample_is_not_walked`、`tests/test_changes_check.py::test_a_change_that_stops_the_sample_path_is_a_result_named_failure_by_the_change`、`tests/test_changes_check.py::test_a_fault_of_the_tool_is_not_a_result_and_the_test_can_be_run_again`、`tests/test_changes_check.py::test_the_test_is_written_down_with_the_versions_the_copy_time_and_the_sample_files`、`tests/test_changes_check.py::test_the_people_who_may_approve_before_and_after_at_every_edge_of_the_ranges`、`tests/test_changes_check.py::test_the_unfinished_tasks_of_production_show_their_due_date_and_condition_before_and_after`、`tests/test_changes_check.py::test_the_test_changes_no_row_of_sandbox_or_production_and_no_global_value`、`tests/test_changes_check.py::test_only_what_git_holds_is_used`、`tests/test_changes.py::test_the_diff_the_widening_and_the_boundaries_are_computed_from_the_two_settings`、`tests/test_changes_web.py::test_a_second_request_is_tested_against_production_and_shows_the_rows_that_change_class` を流す。
- 仮定と制約：試験で何を試すかは手本が見つからない（NIST SP 800-128 は「変更の安全と機能への影響を試す」と書くが、中身は書かない）。今の中身は作者の設計判断。完了した組と差額の処理待ちの組は、照合を流しても変わらないので、本番で照合を流した後では区分が変わる行が少なくなる。

### TC-13 同時の申請・締め・戻し・記録
- 要件：R-11
- 目的：同時の申請が黙って上書きされないこと、締めた期間が開かないこと、戻しの版に今の締めが入ること、記録が書き換えられないことを確かめる。
- 依存：TC-11。
- 対象：`close_kit/changes.py`、`close_kit/versions.py`、`close_kit/db.py`。
- 前提：本番に版がある。
- 入力：同じ本番の版に対して試験した 2 つの申請。試験の後に別の版の番号を渡した適用。締めた期間を開く版。schema 1 の戻し先。追記だけの表への更新と削除。第 2 版のデータベース。
- 期待結果：後の適用は拒否されて試験のやり直しに戻り、先の変更は残る。版の sha256 は適用の時に計算し直され、試験の値と違えば適用されない。締めを開く版は申請の時と適用の時に拒否される。戻しの版には今の締めが入る。確認できない申請からの戻しの下書きは、その申請の戻しの計画（15）の版へ向かう。schema 1 の版は戻し先にできない。ワークブックの読み込みは本番を変えない。追記だけの表と、申請の行（下書きの後）の更新はデータベースが拒否する。利用者の作成は操作の記録に残る。移行は 2 回流しても同じ。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_changes.py::test_the_later_of_two_requests_tested_against_the_same_version_is_refused_at_apply`、`tests/test_changes.py::test_the_move_of_the_pointer_is_one_conditional_update`、`tests/test_changes.py::test_a_version_whose_sha256_differs_from_the_tested_one_is_not_applied`、`tests/test_changes.py::test_the_sha256_is_computed_again_from_the_stored_text_at_apply`、`tests/test_changes.py::test_a_version_cannot_be_applied_under_another_number_even_if_the_text_is_the_same`、`tests/test_changes.py::test_a_version_that_opens_a_period_production_closed_is_refused_when_asked_and_when_applied`、`tests/test_changes.py::test_the_check_of_the_workbook_load_still_compares_with_the_sandbox_only`、`tests/test_changes.py::test_the_rollback_version_has_the_closing_of_production_added`、`tests/test_changes.py::test_a_rollback_to_a_version_of_the_old_schema_is_refused`、`tests/test_changes.py::test_the_rollback_draft_of_a_request_goes_to_the_version_of_its_back_out_plan`、`tests/test_changes.py::test_loading_a_workbook_never_changes_production`、`tests/test_changes.py::test_the_rows_that_may_not_change_are_refused_by_the_database`、`tests/test_changes.py::test_the_third_migration_is_numbered_and_running_it_twice_changes_nothing`、`tests/test_changes.py::test_a_database_of_the_second_release_gets_the_new_tables_and_keeps_its_rows`、`tests/test_changes.py::test_adduser_and_seed_demo_write_the_os_user_to_the_operation_log`、`tests/test_changes.py::test_the_detail_marks_a_person_who_was_made_after_the_request`、`tests/test_changes.py::test_a_person_whose_range_of_amounts_widens_is_marked_when_he_approves_the_result`、`tests/test_changes.py::test_the_list_is_a_csv_for_administrators_and_auditors_with_formula_characters_neutralised`、`tests/test_workbook_versions.py::test_version_and_log_tables_refuse_update_and_delete`、`tests/test_workbook_versions.py::test_only_sandbox_points_at_a_version` を流す。
- 仮定と制約：この PC の OS の利用者がデータベースのファイルを直接書き換えることは、防がない（信頼の境目、`docs/adr/0008`）。

### TC-14 環境の区別と本番の画面・転記ファイル
- 要件：R-11
- 目的：環境が URL と送信の形式の両方に入り、サーバーが毎回同じかを確かめること、本番の版が無い間は本番の画面が止まること、見本の道筋が本番で通り転記ファイルが本番からだけ出ることを確かめる。
- 依存：TC-11。
- 対象：`close_kit/envs.py`、`close_kit/web.py`、`close_kit/posting.py`、`close_kit/web_close.py`。
- 前提：本番に版がある場合とない場合。
- 入力：全部の POST の道に対し、送信の形式の環境を、反対・空・大文字小文字違いにした送信。本番の URL と sandbox の URL への取り込みと照合。本番と sandbox の転記の操作。
- 期待結果：食い違うと 403 で何も変わらない。本番の画面から出した操作は sandbox の行を変えず、逆も同じ。本番の版が無い間、版を要る画面は「本番の版が無い（変更の申請で上げる）」で止まる。本番で取り込み・照合・理由コード・仕訳・調整項目・残高一致表（差 0）・転記ファイルが通る。sandbox の転記の画面は試験用の表示で止まり、転記の操作は拒否されて記録に残る。帯に環境が出て、リンクは同じ環境の中に留まる。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_changes_web.py::test_every_post_of_the_screens_refuses_a_hidden_field_that_names_the_other_environment`、`tests/test_changes_web.py::test_a_post_made_in_one_environment_never_changes_a_row_of_the_other`、`tests/test_changes_web.py::test_production_without_a_version_stops_every_page_that_needs_one`、`tests/test_changes_web.py::test_the_workbook_cannot_be_loaded_into_production_and_loading_never_moves_production`、`tests/test_changes_web.py::test_the_production_workbook_page_offers_no_upload_and_the_sandbox_page_does`、`tests/test_changes_web.py::test_the_band_names_the_environment_and_the_links_stay_in_it`、`tests/test_changes_web.py::test_the_posting_file_over_http_in_production`、`tests/test_close_posting.py::test_heading_columns_environment_and_amounts`、`tests/test_close_posting.py::test_999_journals_are_one_file_and_1000_are_two`、`tests/test_close_posting.py::test_the_same_journal_is_not_written_twice_until_it_is_released`、`tests/test_close_posting.py::test_the_posting_tables_are_append_only`、`tests/test_close_posting.py::test_the_memo_is_cut_to_25_characters_and_the_screen_says_so`、`tests/test_close_posting.py::test_the_file_is_refused_unless_the_unit_is_1`、`tests/test_close_posting.py::test_only_an_administrator_writes_the_file_and_the_log_records_who_and_where`、`tests/test_close_posting.py::test_a_formula_looking_memo_never_reaches_the_file`、`tests/test_close_posting.py::test_the_sandbox_never_writes_a_posting_file`、`tests/test_close_statement.py::test_a_posted_journal_that_came_back_into_the_ledger_is_not_added_twice`、`tests/test_close_statement.py::test_a_posted_journal_is_linked_when_the_ledger_is_imported_not_only_at_the_next_match` を流す。
- 仮定と制約：転記ファイルを SAP に取り込む試験はしていない（`qa-log.md` Q-03）。

### TC-15 検証結果の報告を作る道具
- 要件：R-12
- 目的：検証結果の報告の数が、手で写されず、pytest の出力から作られることを確かめる。
- 依存：全ケース。
- 対象：`tools/test_report.py`、この文書。
- 前提：pytest の `--junitxml` の出力。
- 入力：合格・不合格・飛ばした試験を含む出力。名前が見つからない出力。
- 期待結果：予定 = 合格 + 不合格 + 保留。名前が出力に無い時は何も書かずに止まる。ケースごとの結果は、結ぶ試験が全部合格した時だけ「合格」。報告を作った後でケースが変わると（足した・名前を変えた・試験の名前を足した）、`check` が赤になる。この文書の試験の名前は、全部実在する。要件は R-01 から R-13 まで全部ケースに結ばれている。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_test_report.py::test_the_five_numbers_follow_from_the_junit_file`、`tests/test_test_report.py::test_a_name_that_is_not_in_the_junit_file_stops_the_script_and_writes_nothing`、`tests/test_test_report.py::test_a_parametrised_test_passes_only_when_every_parameter_passed`、`tests/test_test_report.py::test_a_case_without_any_name_or_browser_mark_is_refused`、`tests/test_test_report.py::test_a_report_without_the_generated_block_is_not_written`、`tests/test_test_report.py::test_the_script_compiles_without_a_warning_and_stops_with_exit_code_2_when_a_name_is_missing`、`tests/test_test_report.py::test_check_accepts_later_commits_that_touch_only_documents`、`tests/test_test_report.py::test_check_refuses_a_report_whose_case_rows_no_longer_match_the_cases_file`、`tests/test_test_report.py::test_every_name_in_the_cases_file_is_a_test_that_exists_and_every_requirement_has_a_case` を流す。
- 仮定と制約：報告の commit の hash は、報告を作った時の commit。報告をその後に commit するので、HEAD は 1 つ進む（`tools/test_report.py check` は、その後の変更が文書（docs/ と最上位の .md）だけならよしとする）。

### TC-16 QA 台帳
- 要件：R-13
- 目的：QA 台帳の質問ごとに、調べた事・対応の可否・代替案・出典が空でないことを確かめる。
- 依存：なし。
- 対象：`docs/deliverables/qa-log.md`。
- 前提：なし。
- 入力：QA 台帳の表。
- 期待結果：6 つの質問があり、番号・受けた日・質問・調べた事・対応の可否・代替案・出典・状態が全部埋まり、可否は できる／できない／一部 のどれかで始まり、出典に URL がある。
- 合否の基準：下の試験が合格。
- 手順：`tests/test_test_report.py::test_the_question_log_has_six_questions_with_every_column_filled` を流す。
- 仮定と制約：中身が正しいかは、人が出典と照らして確かめる。

### TC-17 利用者の認証と要求の確かめ
- 要件：R-10
- 目的：ログイン、セッション、CSRF トークン、Origin、Referrer-Policy、ホストの確かめが、全部の POST にかかっていることを確かめる。役割の確かめはサーバーが行う（R-10 の「サーバーが拒否する」の土台）。
- 依存：なし。
- 対象：`close_kit/web.py`、`close_kit/auth.py`。
- 前提：なし。
- 入力：トークンなし・間違い・他のセッションのトークン、Origin なし・`null`・別のホスト、10 回の失敗（9 回までは正しいパスワードで入れる）。
- 期待結果：すべて拒否されて何も変わらない（トークンと Origin の違反は 403）。ログインの時に前のセッションを捨てる。`Referrer-Policy: same-origin` が出る。`Origin: null` は許さない。
- 合否の基準：下の試験が全部合格。
- 手順：`tests/test_web.py::test_login_destroys_the_pre_login_session_and_issues_a_new_id`、`tests/test_web.py::test_wrong_password_keeps_the_pre_session_and_is_logged`、`tests/test_web.py::test_login_failures_are_limited_to_ten`、`tests/test_web.py::test_logout_removes_the_session`、`tests/test_web.py::test_post_without_token_wrong_token_wrong_or_missing_origin_is_refused`、`tests/test_web.py::test_referrer_policy_lets_the_browser_send_the_real_origin_on_form_posts`、`tests/test_web.py::test_token_of_another_session_is_refused`、`tests/test_web.py::test_login_form_post_needs_its_token_too`、`tests/test_web.py::test_other_host_is_refused`、`tests/test_web.py::test_security_headers_and_no_open_docs`、`tests/test_web.py::test_pages_need_a_login`、`tests/test_web.py::test_imported_text_is_escaped_in_the_screens`、`tests/test_web.py::test_oversized_body_is_refused`、`tests/test_close_roles_web.py::test_posts_need_the_token_and_the_origin_like_every_other_post` を流す。
- 仮定と制約：ログインはこの PC の中の物（シングルサインオンは作っていない）。

### TC-18 承認者が画面で仕訳を承認する
- 要件：R-08
- 人が通すケース：はい
- 目的：`manual-users.md` の 3.2.1「仕訳を承認する」の手順どおりにブラウザを操作すると、書いてある画面の名前・ボタン・結果が実物と同じであることを確かめる。
- 依存：TC-08、本番に承認待ちの仕訳があること。
- 対象：`manual-users.md` の 3.2.1。
- 前提：承認者の利用者でログインできる。画面の画像は `docs/deliverables/images/` に置く。
- 入力：マニュアルの手順 1〜7。
- 期待結果：手順 3 で承認待ちの仕訳の番号が出る。手順 5 で「承認した（状態: 承認済み）。」と出る。承認できない仕訳（承認ルートの幅の承認できる人に自分の名前が無い物）では、手順 7 の文が出る。
- 合否の基準：マニュアルに書いた画面の名前・ボタン・結果が、実物と 1 つも違わない。
- 手順：`manual-users.md` の 3.2.1。
- 仮定と制約：自動では合格にならない。
- 実施の記録：2026-10-25。実施：スクリプト（ヘッドレスの Microsoft Edge と HTTP で画面の操作をした。人の目の確かめは別に行う）。デモの DB（`var/` の物）で、127.0.0.1:8765 のサーバーに対して行った。demo-approver で、帯が「環境: production（本番）」→「仕訳」→ 状態「承認待ち」で「絞り込む」→「JE-000004」→ 詳細に日付と期間・借方合計 50・各行の科目名（6100 支払手数料・1100 普通預金）・「承認ルート（金額の幅）0 以上 100,000 未満：demo-approver、demo-approver2」→「承認」で「承認した（状態: 承認済み）。」、状態 承認済み、決めた人 demo-approver。承認できない時：借方合計 120,000 の仕訳（JE-000007）で「この仕訳はあなたは決められない: …」の文が出て、「承認」を押しても同じ文が出て状態は承認待ちのまま（その後、作成者が取り下げた）。画像：`images/01-login.png`、`images/02-home-production.png`、`images/04-journal-detail-approver.png`、`images/05-journal-approved.png`、`images/06-journal-not-allowed.png`。結果：手順の名前・ボタン・結果の文は実物と同じ。

### TC-19 本番で見本の道筋を流す
- 要件：R-09, R-11
- 人が通すケース：はい
- 目的：本番で見本の道筋を流すと、残高一致表の差が 0 になって転記ファイルが出ること、sandbox では転記ファイルが出ないことを、画面で見る。
- 依存：TC-14、TC-20（本番に版があること）。
- 対象：本番の画面（取り込み・照合・理由コード・仕訳・調整項目・残高一致表・転記ファイル）。
- 前提：本番に見本の版がある。見本の 4 つのファイル（`samples/`）。
- 入力：`manual-users.md` と README の手順。
- 期待結果：残高一致表の 2026-10 の差が 0（調整後の銀行残高と帳簿残高が 1,503,987 で一致）。転記ファイルが出る。sandbox の転記の画面は試験用の表示で止まる。
- 合否の基準：差が 0 と画面に出て、転記ファイルが本番だけで出る。
- 手順：README の「見本で試す」を本番の URL（`/production/`）で行う。
- 仮定と制約：自動では合格にならない。
- 実施の記録：2026-10-25。実施：スクリプト（ヘッドレスの Microsoft Edge と HTTP で画面の操作をした。人の目の確かめは別に行う）。デモの DB（`var/` の物）で、127.0.0.1:8765 のサーバーに対して行った。申請 1 で本番 = 版 1 の後、本番に 10 月の帳簿と銀行を取り込み、照合（自動 3/3、差額の処理待ちのグループ 1）、R01 をグループ（150,000／149,950）に、R04 を帳簿 2026-10-31 70,000 に、R03 を銀行 2026-10-25 37 に付け、仕訳 JE-000004（50）・JE-000005（37）を申請、demo-approver が両方と調整項目を承認、11 月の銀行と帳簿を取り込み、照合をやり直した。残高一致表は 2026-10（調整後 1,503,987 で両方）と 2026-11 で「原因の分からない差は無い」。本番の転記ファイル `journal_post_production_..._1of1.csv` に仕訳 2 件・4 行、先頭の行「# 環境: production（本番）」。sandbox の転記ファイルの画面は 409 で「試験用。転記ファイルは production から出す（sandbox では出さない）」。画像：なし。結果：合格。

### TC-20 変更の申請を画面で通す
- 要件：R-11
- 人が通すケース：はい
- 目的：見本のワークブックを本番へ上げる申請を、画面で最後まで通し、続けて許容差を変えた申請で、試験の結果に前と後の件数と区分が変わった行が並ぶことを見る。申請者が自分の申請を承認しようとすると拒否されることを見る。
- 依存：TC-11。
- 対象：変更の申請の画面。`manual-admins.md` の 3.2。
- 前提：`demo-admin`（申請者）、`demo-admin2`（事前承認と適用）、`demo-approver`（結果の承認）。
- 入力：`manual-admins.md` の 3.2 の手順。
- 期待結果：本番の版が入る。2 つ目の申請の試験の結果に、区分ごとの件数の前と後、区分が変わった行、境目の金額で承認できる人の前と後が並ぶ。申請者の承認の操作は 403。
- 合否の基準：マニュアルの手順どおりに進み、画面に書いてある物が出る。
- 手順：`manual-admins.md` の 3.2。
- 仮定と制約：自動では合格にならない。
- 実施の記録：2026-10-25。実施：スクリプト（ヘッドレスの Microsoft Edge と HTTP で画面の操作をした。人の目の確かめは別に行う）。デモの DB（`var/` の物）で、127.0.0.1:8765 のサーバーに対して行った。申請 1「見本のワークブックを本番へ」を demo-admin が作り申請、demo-admin の画面には事前承認のボタンが無い、demo-admin2 が事前承認（試験 成功・見本の道筋の差 0）、demo-approver と demo-admin2 が結果を承認（2 人そろった）、demo-admin2 が適用、demo-admin が確認 → 確認済み、本番 = 版 1。申請 2「許容差 100→0 と承認ルートの閾値 10 万→20 万」：試験の結果 = 失敗（変更の影響）、前と後 自動 6→4・候補 0→2・未照合 3→3、区分が変わった行 2 件（INV-2610-003 の帳簿 2026-10-04 150,000 と銀行 2026-10-05 149,950 が 自動→候補）、境目の承認できる人の前と後（100,000 と 199,999 で demo-approver2 → demo-approver・demo-approver2）。demo-admin が結果の承認を直接 POST すると 403「申請者は、自分の申請の承認・却下・適用ができない（役割をいくつ持っていても）」。申請 2 は試験済みのまま置いた。画像：`images/10-change-result.png`、`images/15-changes-list.png`。結果：合格。

## 付録 A：変更の記録
| 日付 | 変更 |
|---|---|
| 2026-10-25 | 最初の版。 |
| 2026-10-25 | 見直しで見つけた不具合を直し、それを確かめる試験の名前を TC-08・TC-11・TC-13・TC-14・TC-15 に足した。 |

## 付録 B：用語集
`manual-users.md` の付録 B。
