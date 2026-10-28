"""Record layouts of the 全銀協 deposit/withdrawal statement (fixed length, 200 bytes per record).

Positions and meanings follow the field tables in the MUFG Trust & Banking statement-inquiry
manual ("入出金明細照会［全銀］（全銀協規定形式）"). Only item names, types and widths are used; no sample
values are copied. Type "N" = digits (right aligned, zero padded), "C" = half-width characters
(left aligned, space padded).
"""
from __future__ import annotations

RECORD_LENGTH = 200

# (item name, type, width)
HEADER = [
    ("データ区分", "N", 1), ("種別コード", "N", 2), ("コード区分", "N", 1), ("作成日", "N", 6),
    ("勘定日（自）", "N", 6), ("勘定日（至）", "N", 6), ("銀行コード", "N", 4), ("銀行名", "C", 15),
    ("支店コード", "N", 3), ("支店名", "C", 15), ("ダミー1", "N", 3), ("預金種目", "N", 1),
    ("口座番号", "N", 10), ("口座名", "C", 40), ("貸越区分", "N", 1), ("通帳・証書区分", "N", 1),
    ("取引前残高", "N", 14), ("ダミー2", "C", 71),
]
DATA = [
    ("データ区分", "N", 1), ("照会番号", "N", 8), ("勘定日", "N", 6), ("預入・払出日", "N", 6),
    ("入払区分", "N", 1), ("取引区分", "N", 2), ("取引金額", "N", 12), ("うち他店券金額", "N", 12),
    ("交換呈示日", "N", 6), ("不渡返還日", "N", 6), ("手形・小切手区分", "N", 1),
    ("手形・小切手番号", "N", 7), ("僚店番号", "N", 3), ("振込依頼人コード", "N", 10),
    ("振込依頼人名または契約者番号", "C", 48), ("仕向銀行名", "C", 15), ("仕向店名", "C", 15),
    ("摘要内容", "C", 20), ("EDI情報", "C", 20), ("ダミー", "C", 1),
]
TRAILER = [
    ("データ区分", "N", 1), ("入金件数", "N", 6), ("入金額合計", "N", 13), ("出金件数", "N", 6),
    ("出金額合計", "N", 13), ("貸越区分", "N", 1), ("取引後残高", "N", 14),
    ("データ・レコード件数", "N", 7), ("ダミー", "C", 139),
]
END = [
    ("データ区分", "N", 1), ("レコード総件数", "N", 10), ("口座数", "N", 5), ("ダミー", "C", 184),
]

LAYOUTS = {"1": HEADER, "2": DATA, "8": TRAILER, "9": END}
KIND_NAMES = {"1": "ヘッダー", "2": "データ", "8": "トレーラ", "9": "エンド"}


def total_width(layout: list[tuple[str, str, int]]) -> int:
    return sum(w for _, _, w in layout)
