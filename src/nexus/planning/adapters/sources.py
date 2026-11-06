"""Adaptadores tipados por fuente. Conservan identidades exactas (sin recortar espacios de las
claves de cliente), procedencia por fila y corte; los números se leen con ``Decimal`` exacto y lo
que no es numérico queda como desconocido (``None``), nunca como cero."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from nexus.planning.adapters.tables import Row, SourceTable, with_cut
from nexus.planning.model import SourceRef, parse_decimal
from nexus.planning.rules import PlanningRules


def _date(raw: str, formats: tuple[str, ...]) -> str | None:
    text = raw.strip()
    for fmt_ in formats:
        try:
            return dt.datetime.strptime(text, fmt_).date().isoformat()
        except ValueError:
            continue
    return None


def _ts(raw: str) -> str | None:
    text = raw.strip()
    if not text or text.upper() == "NULL":
        return None
    for fmt_ in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y %H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt_).isoformat()
        except ValueError:
            continue
    return None


_DUE = ("%m/%d/%y", "%m/%d/%Y")
_PORTAL_DATE = ("%m/%d/%Y", "%m/%d/%y")


@dataclass(frozen=True)
class Upstream:
    file: str
    sha256: str
    line: str

    def as_dict(self) -> dict[str, str]:
        return {"file": self.file, "sha256": self.sha256, "line": self.line}


@dataclass(frozen=True)
class BacklogLine:
    item: str
    snapshot: str
    customer_key: str  # exacta, con espacios significativos
    customer_name: str
    order: str  # conserva ceros iniciales
    line: str
    due_date: str | None
    due_raw: str
    qty_order: Decimal | None
    qty_open: Decimal | None
    qty_alloc: Decimal | None
    upstream: Upstream
    ref: SourceRef

    @property
    def identity(self) -> tuple[str, str, str, str, str]:
        return (self.snapshot, self.item, self.customer_key, self.order, self.line)


@dataclass(frozen=True)
class FgSnapshot:
    item: str
    snapshot: str
    physical: Decimal | None
    available: Decimal | None
    upstream: Upstream
    ref: SourceRef


@dataclass(frozen=True)
class PortalLine:
    item: str
    item_portal: str
    line_id: str
    status: str
    ordered_raw: str
    shipped_raw: str
    ship_date: str | None
    delivery_date: str | None
    map_status: str
    provided_decision: str  # columna del proveedor; solo se usa como oráculo
    ref: SourceRef


@dataclass(frozen=True)
class BillingMove:
    item: str
    date: str | None
    customer_key: str
    customer_name: str
    document: str
    series: str
    uom: str
    qty: Decimal | None
    ref: SourceRef


@dataclass(frozen=True)
class InventoryRecord:
    item: str
    stockroom: str
    qty: Decimal | None
    cut: str | None
    ref: SourceRef


@dataclass(frozen=True)
class WorkOrder:
    company: str
    order_no: str
    order_type: str
    item: str
    status_current: str  # estado ACTUAL del snapshot; vacío no es "abierta en octubre"
    order_qty: Decimal | None
    customer_code: str
    snapshot_date: str
    act_date_raw: str
    ref: SourceRef

    @property
    def key(self) -> tuple[str, str]:
        return (self.company, self.order_no)


@dataclass(frozen=True)
class RouteOp:
    company: str
    order_no: str
    item: str
    seq: int
    work_center: str
    oper_cd: str
    oper_status: str
    pln_qty: Decimal | None
    pln_hrs: Decimal | None
    set_std: Decimal | None
    mch_std: Decimal | None
    e_or_s_std: str
    time_basis_cd: str
    act_qty: Decimal | None
    act_hrs: Decimal | None
    scrap_qty: Decimal | None
    snapshot_date: str
    ref: SourceRef


@dataclass(frozen=True)
class LaborGroup:
    item: str
    company: str
    order_no: str
    seq: int
    work_center: str
    record_type: str
    oper_status: str
    transaction_rows: int | None
    qty: Decimal | None
    scrap: Decimal | None
    posted_by_month_end: Decimal | None
    late_rows: int | None
    first_activity: str | None
    last_activity: str | None
    ref: SourceRef

    @property
    def key(self) -> tuple[str, str, int, str, str, str]:
        return (
            self.company,
            self.order_no,
            self.seq,
            self.work_center,
            self.record_type,
            self.oper_status,
        )


@dataclass(frozen=True)
class Correction:
    corr_id: str
    item: str
    order_no: str
    seq: int
    activity_date: str | None
    correction_ts: str | None
    added_qty: Decimal | None
    added_scrap: Decimal | None
    correction_type: str
    matched_labor: str
    matched_adjustments: str
    ref: SourceRef


@dataclass(frozen=True)
class LateAdjustment:
    item: str
    company: str
    order_no: str
    seq: int
    record_type: str
    activity_ts: str | None
    transaction_ts: str | None
    qty: Decimal | None
    scrap: Decimal | None
    reversal: str
    ref: SourceRef


def _int(raw: str) -> int | None:
    value = parse_decimal(raw)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


def _seq(raw: str) -> int:
    value = _int(raw)
    if value is None:
        raise ValueError(f"secuencia de operación no entera: {raw!r}")
    return value


def backlog(table: SourceTable, rules: PlanningRules) -> list[BacklogLine]:
    out = []
    for r in table.rows:
        snapshot = rules.snapshot_date(r["source_file"])
        row = with_cut(r, snapshot)
        out.append(
            BacklogLine(
                item=r["item"],
                snapshot=snapshot or "UNKNOWN",
                customer_key=r["customer_key_exact"],
                customer_name=r["customer_name_sales_raw"],
                order=r["order"],
                line=r["line"],
                due_date=_date(r["date_due_raw"], _DUE),
                due_raw=r["date_due_raw"],
                qty_order=parse_decimal(r["qty_order"]),
                qty_open=parse_decimal(r["qty_open"]),
                qty_alloc=parse_decimal(r["qty_alloc"]),
                upstream=Upstream(r["source_file"], r["source_sha256"], r["source_line"]),
                ref=row.ref,
            )
        )
    return out


def fg_snapshots(table: SourceTable, rules: PlanningRules) -> list[FgSnapshot]:
    out = []
    for r in table.rows:
        snapshot = rules.snapshot_date(r["source_file"])
        out.append(
            FgSnapshot(
                item=r["item"],
                snapshot=snapshot or "UNKNOWN",
                physical=parse_decimal(r["physical_stock"]),
                available=parse_decimal(r["available_stock"]),
                upstream=Upstream(r["source_file"], r["source_sha256"], r["source_line"]),
                ref=with_cut(r, snapshot).ref,
            )
        )
    return out


def portal(table: SourceTable) -> list[PortalLine]:
    return [
        PortalLine(
            item=r["ITEM homologado"],
            item_portal=r["ITEM portal original"],
            line_id=r["Line ID"],
            status=r["Status"],
            ordered_raw=r["Ordered raw"],
            shipped_raw=r["Shipped raw"],
            ship_date=_date(r["Ship date"], _PORTAL_DATE),
            delivery_date=_date(r["Delivery date"], _PORTAL_DATE),
            map_status=r["Map status"],
            provided_decision=r.get("Decisión de línea"),
            ref=r.ref,
        )
        for r in table.rows
    ]


def billing(table: SourceTable) -> list[BillingMove]:
    return [
        BillingMove(
            item=r["ITEM"],
            date=_date(r["Fecha factura"], ("%Y-%m-%d",)),
            customer_key=r["Cliente exacto"],
            customer_name=r["Nombre cliente"],
            document=r["Documento"],
            series=r["Serie"],
            uom=r["UOM"],
            qty=parse_decimal(r["Qty firmada"]),
            ref=r.ref,
        )
        for r in table.rows
    ]


def inventory(table: SourceTable) -> list[InventoryRecord]:
    out = []
    for r in table.rows:
        cut = _ts(r["Corte de inventario"])
        out.append(
            InventoryRecord(
                item=r["ITEM"],
                stockroom=r["Stockroom"],
                qty=parse_decimal(r["Cantidad física"]),
                cut=cut,
                ref=with_cut(r, cut).ref,
            )
        )
    return out


def work_orders(table: SourceTable) -> list[WorkOrder]:
    return [
        WorkOrder(
            company=r["company_cd"],
            order_no=r["order_no"],
            order_type=r["order_type"],
            item=r["part_number"],
            status_current=r["order_status"],
            order_qty=parse_decimal(r["order_qty"]),
            customer_code=r["cust_code"],
            snapshot_date=r["snapshot_date"],
            act_date_raw=r["act_date"],
            ref=with_cut(r, r["snapshot_date"]).ref,
        )
        for r in table.rows
    ]


def routing(table: SourceTable) -> list[RouteOp]:
    return [
        RouteOp(
            company=r["company_cd"],
            order_no=r["order_no"],
            item=r["part_number"],
            seq=_seq(r["seq_numb"]),
            work_center=r["wc_id"],
            oper_cd=r["oper_cd"],
            oper_status=r["oper_status"],
            pln_qty=parse_decimal(r["pln_qty"]),
            pln_hrs=parse_decimal(r["pln_hrs"]),
            set_std=parse_decimal(r["set_std"]),
            mch_std=parse_decimal(r["mch_std"]),
            e_or_s_std=r["e_or_s_std"],
            time_basis_cd=r["time_basis_cd"],
            act_qty=parse_decimal(r["act_qty"]),
            act_hrs=parse_decimal(r["act_hrs"]),
            scrap_qty=parse_decimal(r["scrap_qty"]),
            snapshot_date=r["snapshot_date"],
            ref=with_cut(r, r["snapshot_date"]).ref,
        )
        for r in table.rows
    ]


def labor_groups(table: SourceTable) -> list[LaborGroup]:
    return [
        LaborGroup(
            item=r["Item"],
            company=r["Company"],
            order_no=r["WorkOrder"],
            seq=_seq(r["OperationSequence"]),
            work_center=r["WorkCenter"],
            record_type=r["RecordType"],
            oper_status=r["OperationStatus"],
            transaction_rows=_int(r["TransactionRows"]),
            qty=parse_decimal(r["OctoberOperationQty"]),
            scrap=parse_decimal(r["OctoberScrapQty"]),
            posted_by_month_end=parse_decimal(r["QtyPostedByOct31"]),
            late_rows=_int(r["LatePostingRows"]),
            first_activity=_ts(r["FirstActivity"]),
            last_activity=_ts(r["LastActivity"]),
            ref=r.ref,
        )
        for r in table.rows
    ]


def corrections(table: SourceTable) -> list[Correction]:
    return [
        Correction(
            corr_id=r["CorrID"],
            item=r["Item"],
            order_no=r["WorkOrder"],
            seq=_seq(r["OperationSeq"]),
            activity_date=_date(r["ActivityDate"], ("%Y-%m-%d",)),
            correction_ts=_ts(r["CorrectionDate"]),
            added_qty=parse_decimal(r["AddedQty"]),
            added_scrap=parse_decimal(r["AddedScrap"]),
            correction_type=r["CorrectionType"],
            matched_labor=r["MatchedLabor"],
            matched_adjustments=r["MatchedAdjustments"],
            ref=r.ref,
        )
        for r in table.rows
    ]


def late_adjustments(table: SourceTable) -> list[LateAdjustment]:
    return [
        LateAdjustment(
            item=r["Item"],
            company=r["Company"],
            order_no=r["WorkOrder"],
            seq=_seq(r["OperationSeq"]),
            record_type=r["RecordType"],
            activity_ts=_ts(r["ActivityDate"]),
            transaction_ts=_ts(r["AdjustmentTransactionDate"]),
            qty=parse_decimal(r["ActivityQty"]),
            scrap=parse_decimal(r["ScrapQty"]),
            reversal=r["ReversalFlag"],
            ref=r.ref,
        )
        for r in table.rows
    ]


def membership(table: SourceTable, company_col: str, order_col: str) -> set[tuple[str, str]]:
    return {(r[company_col], r[order_col]) for r in table.rows}


def rows_by(table: SourceTable, column: str) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
    for r in table.rows:
        out.setdefault(r[column], []).append(r)
    return out


def as_record(obj: Any) -> dict[str, Any]:
    """Representación serializable de un registro tipado (para la salida navegable)."""
    out = {}
    for key, value in obj.__dict__.items():
        if key == "ref":
            continue
        out[key] = value.as_dict() if isinstance(value, Upstream) else value
    out["source"] = {"file": obj.ref.file, "line": obj.ref.line, "cut": obj.ref.cut}
    return out
