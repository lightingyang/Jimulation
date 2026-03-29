# -*- coding: utf-8 -*-
"""Coil uncoiling scheduling engine."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
import math

from core.config_loader import load_preprocessing_config, load_uncoiling_vendor_config
from core.coil_scheduling.state import ScheduleState


STEEL_DENSITY = 7.85
MAX_DAILY_MINUTES = 1440.0
DAY_SHIFT_MINUTES = 720.0
DEFAULT_UNCOILING_EFFICIENCY_RATIO = 0.8
SEGMENTS_PER_DAY = 2
EPSILON = 1e-9

_preprocessing_cfg = load_preprocessing_config()
UNCOILING_SPEED = _preprocessing_cfg.get("line_speed", 35) * 1000.0
DEVICE_OEE = float(_preprocessing_cfg.get("device_oee", 0.8))

_manufacturer_efficiency_cfg = load_uncoiling_vendor_config()


@dataclass(frozen=True, slots=True)
class UncoilingTask:
    input_index: int
    process_day: int
    scheduling_day: int
    strict_latest_finish: float
    relaxed_latest_finish: float
    part_id: str
    work_order: str
    manufacturer: Optional[str]
    sheet_count: int
    coil_length: float
    coil_width: float
    coil_thickness: float
    spec_prefix: Optional[str]
    weight_per_sheet: float
    time_per_sheet: float
    efficiency_ratio: float


@dataclass(slots=True)
class ScheduledTaskSlice:
    task: UncoilingTask
    assigned_day: int
    sheet_count: int = 0
    total_weight: float = 0.0
    total_time: float = 0.0
    segment_minutes: List[float] = field(default_factory=lambda: [0.0, 0.0])

    def add_allocation(self, additional_sheets: int, additional_segment_minutes: List[float]) -> None:
        if additional_sheets <= 0:
            return

        added_minutes = sum(additional_segment_minutes)
        self.sheet_count += additional_sheets
        self.total_weight += self.task.weight_per_sheet * additional_sheets
        self.total_time += added_minutes

        for segment_index, minutes in enumerate(additional_segment_minutes):
            self.segment_minutes[segment_index] += minutes

    def remove_sheets(self, removed_sheets: int) -> Tuple[float, List[float]]:
        if removed_sheets <= 0:
            return 0.0, [0.0 for _ in range(SEGMENTS_PER_DAY)]

        removed_time = self.task.time_per_sheet * removed_sheets
        removed_segment_minutes = [0.0 for _ in range(SEGMENTS_PER_DAY)]
        remaining_to_remove = removed_time

        for segment_index in range(SEGMENTS_PER_DAY - 1, -1, -1):
            available = self.segment_minutes[segment_index]
            if available <= EPSILON:
                continue

            consumed = min(available, remaining_to_remove)
            self.segment_minutes[segment_index] -= consumed
            removed_segment_minutes[segment_index] += consumed
            remaining_to_remove -= consumed

            if remaining_to_remove <= EPSILON:
                remaining_to_remove = 0.0
                break

        if remaining_to_remove > 1e-6:
            raise ValueError("scheduled slice segment minutes are inconsistent")

        self.sheet_count -= removed_sheets
        self.total_weight -= self.task.weight_per_sheet * removed_sheets
        self.total_time -= removed_time
        return removed_time, removed_segment_minutes


@dataclass(slots=True)
class UncoilingBatch:
    strict_latest_finish: float
    relaxed_latest_finish: float
    spec_prefix: Optional[str]
    coil_length: float
    coil_width: float
    coil_thickness: float
    efficiency_ratio: float
    time_per_sheet: float
    tasks: List[UncoilingTask]
    first_input_index: int
    remaining_sheet_count: int
    remaining_by_task: Dict[int, int]

    @property
    def total_time(self) -> float:
        return self.time_per_sheet * self.remaining_sheet_count


def calc_sheet_weight(length: float, width: float, thickness: float) -> float:
    return (length / 1000) * (width / 1000) * (thickness / 1000) * STEEL_DENSITY


def _get_manufacturer_efficiency_ratio(manufacturer: Optional[str]) -> float:
    default_config = _manufacturer_efficiency_cfg.get("default", {})
    default_ratio = default_config.get("efficiency_ratio", DEFAULT_UNCOILING_EFFICIENCY_RATIO)

    selected_name = (manufacturer or "").strip()
    if not selected_name:
        return 1.0

    selected_config = _manufacturer_efficiency_cfg.get(selected_name)
    ratio = (selected_config or default_config).get("efficiency_ratio", default_ratio)

    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        ratio = float(default_ratio)

    return ratio if ratio > 0 else float(default_ratio)


def calc_sheet_time(length: float, efficiency_ratio: float = 1.0) -> float:
    effective_ratio = efficiency_ratio if efficiency_ratio > 0 else 1.0
    effective_oee = DEVICE_OEE if DEVICE_OEE > 0 else 1.0
    return length / UNCOILING_SPEED / effective_ratio / effective_oee


def build_uncoiling_tasks(
    daily_parts,
    scheduling_window_days,
    total_days,
    lead_time_minutes,
) -> List[UncoilingTask]:
    max_process_day = max(d.day for d in daily_parts)
    window = scheduling_window_days if scheduling_window_days else total_days
    offset = window - max_process_day

    tasks: List[UncoilingTask] = []
    input_index = 0

    for daily in daily_parts:
        process_day = daily.day
        scheduling_day = offset + process_day
        scheduling_day_finish = scheduling_day * MAX_DAILY_MINUTES
        strict_latest_finish = scheduling_day_finish - max(float(lead_time_minutes), 0.0)
        relaxed_latest_finish = max(strict_latest_finish, scheduling_day_finish - DAY_SHIFT_MINUTES)

        for part in daily.parts:
            if part.sheet_count < 1:
                raise ValueError(f"part_id={part.part_id or '<empty>'} 的 sheet_count 必须大于 0")

            weight_per_sheet = calc_sheet_weight(part.coil_length, part.coil_width, part.coil_thickness)
            efficiency_ratio = _get_manufacturer_efficiency_ratio(part.manufacturer)
            time_per_sheet = calc_sheet_time(part.coil_length, efficiency_ratio)

            if time_per_sheet > MAX_DAILY_MINUTES:
                raise ValueError(
                    f"part_id={part.part_id or '<empty>'} 的单张开卷时间 {time_per_sheet:.4f} 分钟，"
                    f"已超过单日上限 {MAX_DAILY_MINUTES:.0f} 分钟"
                )

            tasks.append(
                UncoilingTask(
                    input_index=input_index,
                    process_day=process_day,
                    scheduling_day=scheduling_day,
                    strict_latest_finish=strict_latest_finish,
                    relaxed_latest_finish=relaxed_latest_finish,
                    part_id=part.part_id,
                    work_order=part.work_order,
                    manufacturer=part.manufacturer,
                    sheet_count=part.sheet_count,
                    coil_length=part.coil_length,
                    coil_width=part.coil_width,
                    coil_thickness=part.coil_thickness,
                    spec_prefix=part.spec_prefix,
                    weight_per_sheet=weight_per_sheet,
                    time_per_sheet=time_per_sheet,
                    efficiency_ratio=efficiency_ratio,
                )
            )
            input_index += 1

    return tasks


def build_uncoiling_batches(tasks: List[UncoilingTask]) -> List[UncoilingBatch]:
    batch_map: Dict[Tuple[Any, ...], UncoilingBatch] = {}

    for task in tasks:
        batch_key = (
            task.strict_latest_finish,
            task.relaxed_latest_finish,
            task.spec_prefix,
            task.coil_length,
            task.coil_width,
            task.coil_thickness,
            task.efficiency_ratio,
        )
        batch = batch_map.get(batch_key)

        if batch is None:
            batch = UncoilingBatch(
                strict_latest_finish=task.strict_latest_finish,
                relaxed_latest_finish=task.relaxed_latest_finish,
                spec_prefix=task.spec_prefix,
                coil_length=task.coil_length,
                coil_width=task.coil_width,
                coil_thickness=task.coil_thickness,
                efficiency_ratio=task.efficiency_ratio,
                time_per_sheet=task.time_per_sheet,
                tasks=[],
                first_input_index=task.input_index,
                remaining_sheet_count=0,
                remaining_by_task={},
            )
            batch_map[batch_key] = batch

        batch.tasks.append(task)
        batch.remaining_sheet_count += task.sheet_count
        batch.remaining_by_task[task.input_index] = task.sheet_count
        batch.first_input_index = min(batch.first_input_index, task.input_index)

    for batch in batch_map.values():
        batch.tasks.sort(key=lambda task: task.input_index)

    return list(batch_map.values())


def _clone_uncoiling_batch(batch: UncoilingBatch) -> UncoilingBatch:
    return UncoilingBatch(
        strict_latest_finish=batch.strict_latest_finish,
        relaxed_latest_finish=batch.relaxed_latest_finish,
        spec_prefix=batch.spec_prefix,
        coil_length=batch.coil_length,
        coil_width=batch.coil_width,
        coil_thickness=batch.coil_thickness,
        efficiency_ratio=batch.efficiency_ratio,
        time_per_sheet=batch.time_per_sheet,
        tasks=batch.tasks,
        first_input_index=batch.first_input_index,
        remaining_sheet_count=batch.remaining_sheet_count,
        remaining_by_task=dict(batch.remaining_by_task),
    )


def _spec_key(obj) -> Tuple[Any, ...]:
    return (
        obj.spec_prefix,
        obj.coil_length,
        obj.coil_width,
        obj.coil_thickness,
        obj.efficiency_ratio,
    )


def _max_assignable_sheets(remaining_minutes: float, time_per_sheet: float) -> int:
    if remaining_minutes <= EPSILON or time_per_sheet <= EPSILON:
        return 0
    return max(int((remaining_minutes + EPSILON) // time_per_sheet), 0)


def _latest_finish_day(latest_finish: float) -> int:
    if latest_finish <= EPSILON:
        return 0
    return max(int(math.ceil(latest_finish / MAX_DAILY_MINUTES - EPSILON)), 0)


def _get_day_segment_limits(latest_finish: float, day: int) -> List[float]:
    day_start = (day - 1) * MAX_DAILY_MINUTES
    limits: List[float] = []

    for segment_index in range(SEGMENTS_PER_DAY):
        segment_start = day_start + segment_index * DAY_SHIFT_MINUTES
        segment_end = segment_start + DAY_SHIFT_MINUTES
        limits.append(max(min(segment_end, latest_finish) - segment_start, 0.0))

    return limits


def _get_day_available_segment_minutes(
    day: int,
    segment_limits: List[float],
    day_segment_used: Dict[int, List[float]],
) -> List[float]:
    used_segments = day_segment_used.get(day, [0.0 for _ in range(SEGMENTS_PER_DAY)])
    return [max(limit - used_segments[index], 0.0) for index, limit in enumerate(segment_limits)]


def _allocate_segment_minutes(
    segment_minutes: List[float],
    required_minutes: float,
    *,
    mutate: bool = False,
) -> List[float]:
    allocated = [0.0 for _ in range(SEGMENTS_PER_DAY)]
    remaining = required_minutes

    for segment_index in range(SEGMENTS_PER_DAY - 1, -1, -1):
        available = segment_minutes[segment_index]
        if available <= EPSILON:
            continue

        consumed = min(available, remaining)
        allocated[segment_index] = consumed
        if mutate:
            segment_minutes[segment_index] -= consumed
        remaining -= consumed

        if remaining <= EPSILON:
            remaining = 0.0
            break

    if remaining > 1e-6:
        raise ValueError("segment capacity allocation mismatch")

    return allocated


def _add_task_slice(
    state: ScheduleState,
    task: UncoilingTask,
    assigned_day: int,
    sheet_count: int,
    segment_minutes: List[float],
) -> None:
    if sheet_count <= 0:
        return

    day_entries = state.day_task_map.setdefault(assigned_day, {})
    task_slice = day_entries.get(task.input_index)
    if task_slice is None:
        task_slice = ScheduledTaskSlice(task=task, assigned_day=assigned_day)
        day_entries[task.input_index] = task_slice
    task_slice.add_allocation(sheet_count, segment_minutes)

    used_segments = state.day_segment_used.setdefault(assigned_day, [0.0 for _ in range(SEGMENTS_PER_DAY)])
    for segment_index, minutes in enumerate(segment_minutes):
        used_segments[segment_index] += minutes

    spec_key = _spec_key(task)
    spec_counts = state.day_spec_sheet_counts.setdefault(assigned_day, {})
    spec_counts[spec_key] = spec_counts.get(spec_key, 0) + sheet_count


def _remove_task_slice(
    state: ScheduleState,
    donor_day: int,
    donor_slice: ScheduledTaskSlice,
    removed_sheets: int,
) -> float:
    if removed_sheets <= 0:
        return 0.0

    moved_time, removed_segment_minutes = donor_slice.remove_sheets(removed_sheets)
    used_segments = state.day_segment_used.get(donor_day, [0.0 for _ in range(SEGMENTS_PER_DAY)])
    for segment_index, minutes in enumerate(removed_segment_minutes):
        used_segments[segment_index] -= minutes
    if any(minutes > EPSILON for minutes in used_segments):
        state.day_segment_used[donor_day] = used_segments
    else:
        state.day_segment_used.pop(donor_day, None)

    spec_key = _spec_key(donor_slice.task)
    spec_counts = state.day_spec_sheet_counts.get(donor_day, {})
    remaining_spec_sheets = spec_counts.get(spec_key, 0) - removed_sheets
    if remaining_spec_sheets > 0:
        spec_counts[spec_key] = remaining_spec_sheets
    else:
        spec_counts.pop(spec_key, None)
    if not spec_counts:
        state.day_spec_sheet_counts.pop(donor_day, None)

    if donor_slice.sheet_count <= 0:
        state.day_task_map[donor_day].pop(donor_slice.task.input_index, None)
        if not state.day_task_map[donor_day]:
            state.day_task_map.pop(donor_day, None)

    return moved_time


def _move_sheets(
    state: ScheduleState,
    donor_day: int,
    donor_slice: ScheduledTaskSlice,
    receiver_day: int,
    moved_sheets: int,
    segment_minutes: List[float],
) -> None:
    moved_time = _remove_task_slice(state, donor_day, donor_slice, moved_sheets)
    _add_task_slice(state, donor_slice.task, receiver_day, moved_sheets, segment_minutes)
    state.add_day_used(donor_day, -moved_time)
    state.add_day_used(receiver_day, moved_time)


def _assign_batch_sheets(
    batch: UncoilingBatch,
    assigned_day: int,
    sheet_count: int,
    state: ScheduleState,
    segment_minutes: List[float],
) -> None:
    remaining_to_assign = sheet_count
    remaining_segment_minutes = list(segment_minutes)
    for task in batch.tasks:
        task_remaining = batch.remaining_by_task.get(task.input_index, 0)
        if task_remaining <= 0:
            continue

        task_sheets = min(task_remaining, remaining_to_assign)
        task_minutes = batch.time_per_sheet * task_sheets
        task_segment_minutes = _allocate_segment_minutes(remaining_segment_minutes, task_minutes, mutate=True)
        _add_task_slice(
            state,
            task,
            assigned_day,
            task_sheets,
            task_segment_minutes,
        )
        batch.remaining_by_task[task.input_index] = task_remaining - task_sheets
        batch.remaining_sheet_count -= task_sheets
        remaining_to_assign -= task_sheets

        if remaining_to_assign == 0:
            break

    if remaining_to_assign != 0:
        raise ValueError("batch sheet allocation mismatch")

    if sum(remaining_segment_minutes) > 1e-6:
        raise ValueError("batch segment allocation mismatch")


def _get_target_limit(time_per_sheet: float, used_minutes: float) -> Optional[float]:
    if used_minutes < DAY_SHIFT_MINUTES:
        if _max_assignable_sheets(DAY_SHIFT_MINUTES - used_minutes, time_per_sheet) > 0:
            return DAY_SHIFT_MINUTES
    if used_minutes < MAX_DAILY_MINUTES:
        if _max_assignable_sheets(MAX_DAILY_MINUTES - used_minutes, time_per_sheet) > 0:
            return MAX_DAILY_MINUTES
    return None


def _get_batch_latest_finish(batch: UncoilingBatch, use_relaxed_window: bool) -> float:
    return batch.relaxed_latest_finish if use_relaxed_window else batch.strict_latest_finish


def _normalize_rest_days(total_days: int, rest_days: Optional[List[int]]) -> Set[int]:
    if not rest_days:
        return set()
    return {day for day in rest_days if 1 <= day <= total_days}


def _is_weekend_day(day: int) -> bool:
    week_day = ((day - 1) % 7) + 1
    return week_day in (6, 7)


def _rest_day_penalty(day: int, rest_days: Set[int]) -> int:
    if not rest_days:
        return 0
    if day in rest_days:
        return 2
    if _is_weekend_day(day):
        return 1
    return 0


def _rest_candidate_rank(day: int, rest_days: Set[int]) -> int:
    if not rest_days:
        return 2
    if day in rest_days:
        return 0
    if _is_weekend_day(day):
        return 1
    return 2


def _iter_week_ranges(total_days: int) -> List[Tuple[int, int]]:
    return [
        (week_start, min(week_start + 6, total_days))
        for week_start in range(1, total_days + 1, 7)
    ]


def _week_rest_day_stats(state: ScheduleState, week_start: int, week_end: int) -> Tuple[int, int]:
    """Return (full_rest_days, half_rest_days) inside one 7-day bucket."""
    full_rest_days = 0
    half_rest_days = 0

    for day in range(week_start, week_end + 1):
        used_minutes = state.get_day_used(day)
        if used_minutes <= EPSILON:
            full_rest_days += 1
            half_rest_days += 1
        elif used_minutes <= DAY_SHIFT_MINUTES + 1e-6:
            half_rest_days += 1

    return full_rest_days, half_rest_days


def _relocation_segment_limits(task: UncoilingTask, donor_day: int, receiver_day: int) -> List[float]:
    if receiver_day < donor_day:
        return [DAY_SHIFT_MINUTES for _ in range(SEGMENTS_PER_DAY)]
    return _get_day_segment_limits(task.relaxed_latest_finish, receiver_day)


def _batch_window_tightness_key(
    batch: UncoilingBatch,
    use_relaxed_window: bool,
) -> Tuple[float, float, float, float, int]:
    latest_finish = _get_batch_latest_finish(batch, use_relaxed_window)
    available_minutes = max(latest_finish, 0.0)
    load_ratio = batch.total_time / available_minutes if available_minutes > EPSILON else float("inf")
    slack_minutes = max(available_minutes - batch.total_time, 0.0)
    return (-load_ratio, slack_minutes, latest_finish, -batch.total_time, batch.first_input_index)


def _spec_count_after_assignment(
    day_spec_sheet_counts: Dict[int, Dict[Tuple[Any, ...], int]],
    day: int,
    spec_key: Tuple[Any, ...],
) -> Tuple[int, int]:
    spec_counts = day_spec_sheet_counts.get(day, {})
    has_same_spec = 0 if spec_key in spec_counts else 1
    return has_same_spec, len(spec_counts) + has_same_spec


def _score_candidate_day(
    batch: UncoilingBatch,
    day: int,
    used_minutes: float,
    assignable_sheets: int,
    latest_finish: float,
    day_spec_sheet_counts: Dict[int, Dict[Tuple[Any, ...], int]],
    use_relaxed_window: bool,
) -> Tuple[float, ...]:
    assigned_minutes = batch.time_per_sheet * assignable_sheets
    used_after = used_minutes + assigned_minutes
    completion_rank = 0
    if abs(used_after - DAY_SHIFT_MINUTES) > 1e-6 and abs(used_after - MAX_DAILY_MINUTES) > 1e-6:
        completion_rank = 1
    shift_gap_after = min(abs(DAY_SHIFT_MINUTES - used_after), abs(MAX_DAILY_MINUTES - used_after))
    has_same_spec, resulting_spec_count = _spec_count_after_assignment(
        day_spec_sheet_counts, day, _spec_key(batch)
    )

    if use_relaxed_window:
        strict_last_day = _latest_finish_day(batch.strict_latest_finish)
        relaxed_extension_rank = max(day - strict_last_day, 0)
        open_day_rank = 1 if used_minutes <= EPSILON else 0
        return (
            relaxed_extension_rank,
            -assigned_minutes,
            completion_rank,
            shift_gap_after,
            open_day_rank,
            has_same_spec,
            resulting_spec_count,
            day,
        )

    open_day_rank = 1 if used_minutes <= EPSILON else 0
    return (
        -assigned_minutes,
        completion_rank,
        shift_gap_after,
        open_day_rank,
        has_same_spec,
        resulting_spec_count,
        _latest_finish_day(latest_finish) - day,
        -day,
    )


def _select_best_day_for_batch(
    batch: UncoilingBatch,
    total_days: int,
    state: ScheduleState,
    use_relaxed_window: bool,
) -> Tuple[Optional[int], int, Optional[List[float]]]:
    latest_finish = _get_batch_latest_finish(batch, use_relaxed_window)
    start_day = min(_latest_finish_day(latest_finish), total_days)
    best_choice: Optional[Tuple[Tuple[float, ...], int, int, List[float]]] = None

    for day in range(start_day, 0, -1):
        used_minutes = state.get_day_used(day)
        remaining_day_minutes = MAX_DAILY_MINUTES - used_minutes
        if remaining_day_minutes <= EPSILON:
            continue

        segment_limits = _get_day_segment_limits(latest_finish, day)
        available_segment_minutes = _get_day_available_segment_minutes(day, segment_limits, state.day_segment_used)
        available_minutes = min(sum(available_segment_minutes), remaining_day_minutes)
        if available_minutes <= EPSILON:
            continue

        assignable_sheets = min(
            batch.remaining_sheet_count,
            _max_assignable_sheets(available_minutes, batch.time_per_sheet),
        )
        if assignable_sheets <= 0:
            continue

        actual_minutes = batch.time_per_sheet * assignable_sheets
        segment_minutes = _allocate_segment_minutes(available_segment_minutes, actual_minutes)
        choice_key = _score_candidate_day(
            batch=batch,
            day=day,
            used_minutes=used_minutes,
            assignable_sheets=assignable_sheets,
            latest_finish=latest_finish,
            day_spec_sheet_counts=state.day_spec_sheet_counts,
            use_relaxed_window=use_relaxed_window,
        )
        if best_choice is None or choice_key < best_choice[0]:
            best_choice = (choice_key, day, assignable_sheets, segment_minutes)

    if best_choice is None:
        return None, 0, None
    return best_choice[1], best_choice[2], best_choice[3]


def _allocate_batch(
    batch: UncoilingBatch,
    total_days: int,
    state: ScheduleState,
    use_relaxed_window: bool,
) -> None:
    while batch.remaining_sheet_count > 0:
        selected_day, assignable_sheets, segment_minutes = _select_best_day_for_batch(
            batch,
            total_days,
            state,
            use_relaxed_window,
        )
        if selected_day is None or assignable_sheets <= 0 or segment_minutes is None:
            break

        allocated_minutes = batch.time_per_sheet * assignable_sheets
        state.add_day_used(selected_day, allocated_minutes)
        _assign_batch_sheets(
            batch,
            selected_day,
            assignable_sheets,
            state,
            segment_minutes,
        )



def _is_complete_shift(used_minutes: float) -> bool:
    if used_minutes <= EPSILON:
        return True
    return abs(used_minutes - DAY_SHIFT_MINUTES) <= 1e-6 or abs(used_minutes - MAX_DAILY_MINUTES) <= 1e-6


def _completion_distance(used_minutes: float) -> float:
    if used_minutes <= EPSILON:
        return 0.0
    return min(abs(used_minutes - DAY_SHIFT_MINUTES), abs(used_minutes - MAX_DAILY_MINUTES))


def _target_completion_minutes(used_minutes: float) -> Optional[float]:
    if used_minutes <= EPSILON or _is_complete_shift(used_minutes):
        return None
    if used_minutes < 11.0 * 60.0:
        return DAY_SHIFT_MINUTES
    if used_minutes <= 13.0 * 60.0:
        return None
    if used_minutes < 23.0 * 60.0:
        return MAX_DAILY_MINUTES
    return None


def _select_best_fill_full_receiver_day(
    receiver_day: int,
    receiver_used: float,
    total_days: int,
    state: ScheduleState,
) -> Tuple[Optional[int], Optional[ScheduledTaskSlice], int, Optional[List[float]]]:
    receiver_target_minutes = _target_completion_minutes(receiver_used)
    if receiver_target_minutes is None:
        return None, None, 0, None
    receiver_gap_minutes = receiver_target_minutes - receiver_used
    if receiver_gap_minutes <= EPSILON:
        return None, None, 0, None

    best_choice: Optional[Tuple[Tuple[Any, ...], int, ScheduledTaskSlice, int, List[float]]] = None

    for donor_day in range(receiver_day + 1, total_days + 1):
        donor_used = state.get_day_used(donor_day)
        if donor_used <= EPSILON or donor_day not in state.day_task_map:
            continue
        if receiver_target_minutes >= MAX_DAILY_MINUTES - EPSILON and donor_used <= DAY_SHIFT_MINUTES + EPSILON:
            continue

        donor_slices = sorted(
            list(state.day_task_map[donor_day].values()),
            key=lambda item: (-item.total_time, item.task.input_index),
        )
        for donor_slice in donor_slices:
            segment_limits = _get_day_segment_limits(donor_slice.task.relaxed_latest_finish, receiver_day)
            available_segment_minutes = _get_day_available_segment_minutes(
                receiver_day,
                segment_limits,
                state.day_segment_used,
            )
            receiver_capacity = min(sum(available_segment_minutes), receiver_gap_minutes)
            if receiver_capacity <= EPSILON:
                continue

            candidate_targets = [
                min(receiver_capacity, donor_slice.total_time),
                min(receiver_capacity, receiver_gap_minutes),
            ]
            if donor_used > DAY_SHIFT_MINUTES + EPSILON:
                candidate_targets.append(min(receiver_capacity, donor_used - DAY_SHIFT_MINUTES))

            seen_sheet_counts = set()
            for target_minutes in candidate_targets:
                moved_sheets = min(
                    donor_slice.sheet_count,
                    _max_assignable_sheets(target_minutes, donor_slice.task.time_per_sheet),
                )
                if moved_sheets <= 0 or moved_sheets in seen_sheet_counts:
                    continue
                seen_sheet_counts.add(moved_sheets)

                moved_minutes = donor_slice.task.time_per_sheet * moved_sheets
                receiver_after = receiver_used + moved_minutes
                donor_after = donor_used - moved_minutes

                before_incomplete = int(not _is_complete_shift(receiver_used)) + int(not _is_complete_shift(donor_used))
                after_incomplete = int(not _is_complete_shift(receiver_after)) + int(not _is_complete_shift(donor_after))
                if after_incomplete > before_incomplete:
                    continue

                segment_minutes = _allocate_segment_minutes(available_segment_minutes, moved_minutes)
                has_same_spec, resulting_spec_count = _spec_count_after_assignment(
                    state.day_spec_sheet_counts,
                    receiver_day,
                    _spec_key(donor_slice.task),
                )
                choice_key = (
                    after_incomplete,
                    abs(receiver_target_minutes - receiver_after),
                    _completion_distance(donor_after),
                    has_same_spec,
                    resulting_spec_count,
                    donor_day,
                )
                if best_choice is None or choice_key < best_choice[0]:
                    best_choice = (choice_key, donor_day, donor_slice, moved_sheets, segment_minutes)

    if best_choice is None:
        return None, None, 0, None
    return best_choice[1], best_choice[2], best_choice[3], best_choice[4]


def _fill_full_shift_days_from_later(
    total_days: int,
    state: ScheduleState,
) -> None:
    while True:
        state.rebuild_day_used()
        receiver_days = [
            day
            for day, used_minutes in sorted(state.day_used.items())
            if _target_completion_minutes(used_minutes) is not None
        ]
        moved_any = False

        for receiver_day in receiver_days:
            while True:
                receiver_used = state.get_day_used(receiver_day)
                if receiver_used <= EPSILON or _is_complete_shift(receiver_used) or receiver_used >= MAX_DAILY_MINUTES - 1e-6:
                    break

                donor_day, donor_slice, moved_sheets, segment_minutes = _select_best_fill_full_receiver_day(
                    receiver_day,
                    receiver_used,
                    total_days,
                    state,
                )
                if donor_day is None or donor_slice is None or moved_sheets <= 0 or segment_minutes is None:
                    break

                _move_sheets(state, donor_day, donor_slice, receiver_day, moved_sheets, segment_minutes)
                moved_any = True

        if not moved_any:
            break


def _receiver_band_rank(used_minutes: float) -> Tuple[int, float]:
    if 11.0 * 60.0 <= used_minutes <= 13.0 * 60.0:
        return (0, abs(used_minutes - DAY_SHIFT_MINUTES))
    if 23.0 * 60.0 <= used_minutes < MAX_DAILY_MINUTES:
        return (1, abs(used_minutes - MAX_DAILY_MINUTES))
    return (2, _completion_distance(used_minutes))


def _select_best_tiny_tail_receiver_day(
    donor_day: int,
    donor_slice: ScheduledTaskSlice,
    state: ScheduleState,
    blocked_receiver_days: Optional[Set[int]] = None,
) -> Tuple[Optional[int], int, Optional[List[float]]]:
    best_choice: Optional[Tuple[Tuple[Any, ...], int, int, List[float]]] = None
    blocked_days = blocked_receiver_days or set()

    for receiver_day in range(donor_day - 1, 0, -1):
        if receiver_day in blocked_days:
            continue
        receiver_used = state.get_day_used(receiver_day)
        if receiver_used <= EPSILON or receiver_used >= MAX_DAILY_MINUTES - EPSILON:
            continue

        segment_limits = _get_day_segment_limits(donor_slice.task.relaxed_latest_finish, receiver_day)
        available_segment_minutes = _get_day_available_segment_minutes(
            receiver_day,
            segment_limits,
            state.day_segment_used,
        )
        receiver_capacity = min(sum(available_segment_minutes), MAX_DAILY_MINUTES - receiver_used)
        if receiver_capacity <= EPSILON:
            continue

        moved_sheets = min(
            donor_slice.sheet_count,
            _max_assignable_sheets(receiver_capacity, donor_slice.task.time_per_sheet),
        )
        if moved_sheets <= 0:
            continue

        moved_minutes = donor_slice.task.time_per_sheet * moved_sheets
        receiver_after = receiver_used + moved_minutes
        donor_after = state.get_day_used(donor_day) - moved_minutes
        segment_minutes = _allocate_segment_minutes(available_segment_minutes, moved_minutes)
        has_same_spec, resulting_spec_count = _spec_count_after_assignment(
            state.day_spec_sheet_counts,
            receiver_day,
            _spec_key(donor_slice.task),
        )
        choice_key = (
            0 if donor_after <= EPSILON else 1,
            _receiver_band_rank(receiver_used),
            _completion_distance(receiver_after),
            has_same_spec,
            resulting_spec_count,
            -receiver_day,
        )
        if best_choice is None or choice_key < best_choice[0]:
            best_choice = (choice_key, receiver_day, moved_sheets, segment_minutes)

    if best_choice is None:
        return None, 0, None
    return best_choice[1], best_choice[2], best_choice[3]


def _merge_tiny_tail_days_backward(
    total_days: int,
    state: ScheduleState,
    blocked_receiver_days: Optional[Set[int]] = None,
) -> None:
    while True:
        state.rebuild_day_used()
        donor_days = [
            day
            for day, used_minutes in sorted(state.day_used.items(), reverse=True)
            if 0 < used_minutes < 8.0 * 60.0 - EPSILON
        ]
        moved_any = False

        for donor_day in donor_days:
            if donor_day not in state.day_task_map:
                continue

            donor_slices = sorted(
                list(state.day_task_map[donor_day].values()),
                key=lambda item: (-item.total_time, item.task.input_index),
            )
            for donor_slice in donor_slices:
                while donor_day in state.day_task_map and donor_slice.sheet_count > 0:
                    receiver_day, moved_sheets, segment_minutes = _select_best_tiny_tail_receiver_day(
                        donor_day,
                        donor_slice,
                        state,
                        blocked_receiver_days,
                    )
                    if receiver_day is None or moved_sheets <= 0 or segment_minutes is None:
                        break

                    _move_sheets(state, donor_day, donor_slice, receiver_day, moved_sheets, segment_minutes)
                    moved_any = True

        if not moved_any:
            break


def _score_later_receiver_day(
    receiver_day: int,
    receiver_used: float,
    donor_slice: ScheduledTaskSlice,
    moved_sheets: int,
    day_spec_sheet_counts: Dict[int, Dict[Tuple[Any, ...], int]],
) -> Tuple[float, ...]:
    assigned_minutes = donor_slice.task.time_per_sheet * moved_sheets
    has_same_spec, resulting_spec_count = _spec_count_after_assignment(
        day_spec_sheet_counts, receiver_day, _spec_key(donor_slice.task)
    )
    return (
        0 if receiver_used > EPSILON else 1,
        has_same_spec,
        resulting_spec_count,
        abs(DAY_SHIFT_MINUTES - (receiver_used + assigned_minutes)),
        receiver_day,
    )


def _select_best_later_receiver_day(
    donor_day: int,
    donor_slice: ScheduledTaskSlice,
    donor_excess_minutes: float,
    total_days: int,
    state: ScheduleState,
) -> Tuple[Optional[int], int, Optional[List[float]]]:
    best_choice: Optional[Tuple[Tuple[float, ...], int, int, List[float]]] = None
    latest_day = min(total_days, _latest_finish_day(donor_slice.task.relaxed_latest_finish))

    for receiver_day in range(donor_day + 1, latest_day + 1):
        receiver_used = state.get_day_used(receiver_day)
        if receiver_used >= DAY_SHIFT_MINUTES - EPSILON:
            continue

        segment_limits = _get_day_segment_limits(donor_slice.task.relaxed_latest_finish, receiver_day)
        available_segment_minutes = _get_day_available_segment_minutes(
            receiver_day,
            segment_limits,
            state.day_segment_used,
        )
        available_minutes = min(sum(available_segment_minutes), DAY_SHIFT_MINUTES - receiver_used)
        if available_minutes <= EPSILON:
            continue

        moved_sheets = min(
            donor_slice.sheet_count,
            _max_assignable_sheets(available_minutes, donor_slice.task.time_per_sheet),
            _max_assignable_sheets(donor_excess_minutes, donor_slice.task.time_per_sheet),
        )
        if moved_sheets <= 0:
            continue

        actual_minutes = donor_slice.task.time_per_sheet * moved_sheets
        segment_minutes = _allocate_segment_minutes(available_segment_minutes, actual_minutes)
        choice_key = _score_later_receiver_day(
            receiver_day=receiver_day,
            receiver_used=receiver_used,
            donor_slice=donor_slice,
            moved_sheets=moved_sheets,
            day_spec_sheet_counts=state.day_spec_sheet_counts,
        )
        if best_choice is None or choice_key < best_choice[0]:
            best_choice = (choice_key, receiver_day, moved_sheets, segment_minutes)

    if best_choice is None:
        return None, 0, None
    return best_choice[1], best_choice[2], best_choice[3]


def _rebalance_night_shift_days(
    total_days: int,
    state: ScheduleState,
) -> None:
    while True:
        state.rebuild_day_used()
        donor_days = [
            day
            for day, used_minutes in sorted(state.day_used.items())
            if used_minutes > DAY_SHIFT_MINUTES + 1e-6
        ]
        moved_any = False

        for donor_day in donor_days:
            if donor_day not in state.day_task_map:
                continue

            donor_slices = sorted(
                list(state.day_task_map[donor_day].values()),
                key=lambda item: (-item.task.relaxed_latest_finish, -item.total_time, item.task.input_index),
            )

            for donor_slice in donor_slices:
                while donor_day in state.day_task_map and donor_slice.sheet_count > 0:
                    donor_used = state.get_day_used(donor_day)
                    donor_excess_minutes = donor_used - DAY_SHIFT_MINUTES
                    if donor_excess_minutes <= EPSILON:
                        break

                    receiver_day, moved_sheets, segment_minutes = _select_best_later_receiver_day(
                        donor_day,
                        donor_slice,
                        donor_excess_minutes,
                        total_days,
                        state,
                    )
                    if receiver_day is None or moved_sheets <= 0 or segment_minutes is None:
                        break

                    _move_sheets(state, donor_day, donor_slice, receiver_day, moved_sheets, segment_minutes)
                    moved_any = True

        if not moved_any:
            break


def _score_tail_receiver_day(
    receiver_day: int,
    receiver_used: float,
    donor_slice: ScheduledTaskSlice,
    moved_sheets: int,
    day_spec_sheet_counts: Dict[int, Dict[Tuple[Any, ...], int]],
) -> Tuple[float, ...]:
    assigned_minutes = donor_slice.task.time_per_sheet * moved_sheets
    target_limit = _get_target_limit(donor_slice.task.time_per_sheet, receiver_used)
    if target_limit is None:
        target_limit = MAX_DAILY_MINUTES

    has_same_spec, resulting_spec_count = _spec_count_after_assignment(
        day_spec_sheet_counts, receiver_day, _spec_key(donor_slice.task)
    )

    return (
        0 if target_limit == MAX_DAILY_MINUTES else 1,
        target_limit - (receiver_used + assigned_minutes),
        has_same_spec,
        resulting_spec_count,
        -receiver_used,
        -receiver_day,
    )


def _select_best_receiver_day(
    donor_day: int,
    donor_slice: ScheduledTaskSlice,
    state: ScheduleState,
) -> Tuple[Optional[int], int, Optional[List[float]]]:
    best_choice: Optional[Tuple[Tuple[float, ...], int, int, List[float]]] = None
    full_day_limits = [DAY_SHIFT_MINUTES for _ in range(SEGMENTS_PER_DAY)]

    for receiver_day in range(donor_day - 1, 0, -1):
        receiver_used = state.get_day_used(receiver_day)
        if receiver_used <= EPSILON or receiver_used >= MAX_DAILY_MINUTES - EPSILON:
            continue

        available_segment_minutes = _get_day_available_segment_minutes(
            receiver_day,
            full_day_limits,
            state.day_segment_used,
        )
        capacity = sum(available_segment_minutes)
        moved_sheets = min(
            donor_slice.sheet_count,
            _max_assignable_sheets(capacity, donor_slice.task.time_per_sheet),
        )
        if moved_sheets <= 0:
            continue

        actual_minutes = donor_slice.task.time_per_sheet * moved_sheets
        segment_minutes = _allocate_segment_minutes(available_segment_minutes, actual_minutes)
        choice_key = _score_tail_receiver_day(
            receiver_day=receiver_day,
            receiver_used=receiver_used,
            donor_slice=donor_slice,
            moved_sheets=moved_sheets,
            day_spec_sheet_counts=state.day_spec_sheet_counts,
        )
        if best_choice is None or choice_key < best_choice[0]:
            best_choice = (choice_key, receiver_day, moved_sheets, segment_minutes)

    if best_choice is None:
        return None, 0, None
    return best_choice[1], best_choice[2], best_choice[3]


def _compact_short_tail_days(state: ScheduleState) -> None:
    while True:
        state.rebuild_day_used()
        donor_days = [
            day for day, used_minutes in sorted(state.day_used.items(), reverse=True)
            if 0 < used_minutes < DAY_SHIFT_MINUTES - 1e-6
        ]
        moved_any = False

        for donor_day in donor_days:
            if donor_day not in state.day_task_map:
                continue

            donor_slices = sorted(
                list(state.day_task_map[donor_day].values()),
                key=lambda item: (item.total_time, item.task.input_index),
            )

            for donor_slice in donor_slices:
                while donor_day in state.day_task_map and donor_slice.sheet_count > 0:
                    receiver_day, moved_sheets, segment_minutes = _select_best_receiver_day(
                        donor_day,
                        donor_slice,
                        state,
                    )
                    if receiver_day is None or moved_sheets <= 0 or segment_minutes is None:
                        break

                    _move_sheets(state, donor_day, donor_slice, receiver_day, moved_sheets, segment_minutes)
                    moved_any = True

        if not moved_any:
            break


def _score_rest_receiver_day(
    donor_day: int,
    receiver_day: int,
    receiver_used: float,
    donor_slice: ScheduledTaskSlice,
    moved_sheets: int,
    day_spec_sheet_counts: Dict[int, Dict[Tuple[Any, ...], int]],
    rest_days: Set[int],
) -> Tuple[float, ...]:
    assigned_minutes = donor_slice.task.time_per_sheet * moved_sheets
    receiver_after = receiver_used + assigned_minutes
    has_same_spec, resulting_spec_count = _spec_count_after_assignment(
        day_spec_sheet_counts, receiver_day, _spec_key(donor_slice.task)
    )
    return (
        _rest_day_penalty(receiver_day, rest_days),
        0 if receiver_day > donor_day else 1,
        0 if receiver_used > EPSILON else 1,
        has_same_spec,
        resulting_spec_count,
        _completion_distance(receiver_after),
        receiver_day if receiver_day > donor_day else -receiver_day,
    )


def _select_best_rest_receiver_day(
    donor_day: int,
    donor_slice: ScheduledTaskSlice,
    donor_excess_minutes: float,
    total_days: int,
    state: ScheduleState,
    rest_days: Set[int],
    blocked_receiver_days: Set[int],
) -> Tuple[Optional[int], int, Optional[List[float]]]:
    best_choice: Optional[Tuple[Tuple[float, ...], int, int, List[float]]] = None

    for receiver_day in range(1, total_days + 1):
        if receiver_day == donor_day or receiver_day in blocked_receiver_days:
            continue

        receiver_used = state.get_day_used(receiver_day)
        if receiver_used >= MAX_DAILY_MINUTES - EPSILON:
            continue

        segment_limits = _relocation_segment_limits(donor_slice.task, donor_day, receiver_day)
        available_segment_minutes = _get_day_available_segment_minutes(
            receiver_day,
            segment_limits,
            state.day_segment_used,
        )
        available_minutes = min(
            sum(available_segment_minutes),
            MAX_DAILY_MINUTES - receiver_used,
            donor_excess_minutes,
        )
        if available_minutes <= EPSILON:
            continue

        moved_sheets = min(
            donor_slice.sheet_count,
            _max_assignable_sheets(available_minutes, donor_slice.task.time_per_sheet),
        )
        if moved_sheets <= 0:
            continue

        moved_minutes = donor_slice.task.time_per_sheet * moved_sheets
        segment_minutes = _allocate_segment_minutes(available_segment_minutes, moved_minutes)
        choice_key = _score_rest_receiver_day(
            donor_day=donor_day,
            receiver_day=receiver_day,
            receiver_used=receiver_used,
            donor_slice=donor_slice,
            moved_sheets=moved_sheets,
            day_spec_sheet_counts=state.day_spec_sheet_counts,
            rest_days=rest_days,
        )
        if best_choice is None or choice_key < best_choice[0]:
            best_choice = (choice_key, receiver_day, moved_sheets, segment_minutes)

    if best_choice is None:
        return None, 0, None
    return best_choice[1], best_choice[2], best_choice[3]


def _sorted_relocation_slices(state: ScheduleState, donor_day: int) -> List[ScheduledTaskSlice]:
    return sorted(
        list(state.day_task_map[donor_day].values()),
        key=lambda item: (-item.task.relaxed_latest_finish, -item.total_time, item.task.input_index),
    )


def _free_rest_day_load(
    donor_day: int,
    target_minutes: float,
    total_days: int,
    state: ScheduleState,
    rest_days: Set[int],
    blocked_receiver_days: Set[int],
) -> bool:
    moved_any = False

    while donor_day in state.day_task_map and state.get_day_used(donor_day) > target_minutes + EPSILON:
        donor_progress = False
        donor_slices = _sorted_relocation_slices(state, donor_day)

        for donor_slice in donor_slices:
            while donor_day in state.day_task_map and donor_slice.sheet_count > 0:
                donor_excess_minutes = state.get_day_used(donor_day) - target_minutes
                if donor_excess_minutes <= EPSILON:
                    return moved_any

                receiver_day, moved_sheets, segment_minutes = _select_best_rest_receiver_day(
                    donor_day,
                    donor_slice,
                    donor_excess_minutes,
                    total_days,
                    state,
                    rest_days,
                    blocked_receiver_days,
                )
                if receiver_day is None or moved_sheets <= 0 or segment_minutes is None:
                    break

                _move_sheets(state, donor_day, donor_slice, receiver_day, moved_sheets, segment_minutes)
                moved_any = True
                donor_progress = True

        if not donor_progress:
            break

    return moved_any


def _free_preferred_rest_days(
    total_days: int,
    state: ScheduleState,
    rest_days: Set[int],
) -> None:
    if not rest_days:
        return

    blocked_receiver_days = set(rest_days)
    for donor_day in sorted(day for day in rest_days if 1 <= day <= total_days):
        if donor_day not in state.day_task_map:
            continue
        _free_rest_day_load(
            donor_day=donor_day,
            target_minutes=0.0,
            total_days=total_days,
            state=state,
            rest_days=rest_days,
            blocked_receiver_days=blocked_receiver_days,
        )


def _ensure_weekly_rest_days(
    total_days: int,
    state: ScheduleState,
    rest_days: Set[int],
) -> None:
    """Try to preserve weekly rest with minimal disruption.

    Preference order:
    1. produce one full 0h day in the week if possible,
    2. otherwise produce two <=12h half-rest days,
    3. when multiple donor days are possible, pick the one that is easier to free
       and aligns better with preferred rest/weekend days.
    """
    for week_start, week_end in _iter_week_ranges(total_days):
        candidate_days = sorted(
            range(week_start, week_end + 1),
            key=lambda day: (
                _rest_candidate_rank(day, rest_days),
                state.get_day_used(day),
                day,
            ),
        )
        full_rest_candidate_days = sorted(
            range(week_start, week_end + 1),
            key=lambda day: (
                _rest_candidate_rank(day, rest_days),
                state.get_day_used(day),
                -day,
            ),
        )

        full_rest_days, half_rest_days = _week_rest_day_stats(state, week_start, week_end)
        if full_rest_days > 0:
            continue

        # First try to clear one day completely. We allow work to move into other
        # rest days here because "one full day off" is preferred over "two half days".
        for donor_day in full_rest_candidate_days:
            if donor_day not in state.day_task_map:
                continue
            if state.get_day_used(donor_day) <= EPSILON:
                break
            _free_rest_day_load(
                donor_day=donor_day,
                target_minutes=0.0,
                total_days=total_days,
                state=state,
                rest_days=rest_days,
                blocked_receiver_days=set(),
            )
            full_rest_days, half_rest_days = _week_rest_day_stats(state, week_start, week_end)
            if full_rest_days > 0:
                break

        if full_rest_days > 0:
            continue

        if half_rest_days >= 2:
            continue

        # If a full day off is impossible, fall back to two half-rest days and
        # keep the preferred rest-day set protected from receiving tail work again.
        for donor_day in candidate_days:
            if donor_day not in state.day_task_map:
                continue
            if state.get_day_used(donor_day) <= DAY_SHIFT_MINUTES + EPSILON:
                full_rest_days, half_rest_days = _week_rest_day_stats(state, week_start, week_end)
                if half_rest_days >= 2:
                    break
                continue

            _free_rest_day_load(
                donor_day=donor_day,
                target_minutes=DAY_SHIFT_MINUTES,
                total_days=total_days,
                state=state,
                rest_days=rest_days,
                blocked_receiver_days=set(rest_days),
            )
            full_rest_days, half_rest_days = _week_rest_day_stats(state, week_start, week_end)
            if half_rest_days >= 2:
                break


def _normalize_rest_day_to_half_shift(
    total_days: int,
    state: ScheduleState,
    rest_days: Set[int],
) -> None:
    if not rest_days:
        return

    for receiver_day in sorted(day for day in rest_days if 1 <= day <= total_days):
        while True:
            receiver_used = state.get_day_used(receiver_day)
            if receiver_used <= EPSILON:
                break
            if receiver_used >= DAY_SHIFT_MINUTES - EPSILON:
                break

            donor_day, donor_slice, moved_sheets, segment_minutes = _select_best_fill_full_receiver_day(
                receiver_day,
                receiver_used,
                total_days,
                state,
            )
            if donor_day is None or donor_slice is None or moved_sheets <= 0 or segment_minutes is None:
                break

            moved_time = donor_slice.task.time_per_sheet * moved_sheets
            receiver_after = receiver_used + moved_time
            if receiver_after > DAY_SHIFT_MINUTES + EPSILON:
                break

            _move_sheets(state, donor_day, donor_slice, receiver_day, moved_sheets, segment_minutes)


def _schedule_batches_into_state(
    sorted_batches: List[UncoilingBatch],
    total_days: int,
    state: ScheduleState,
) -> None:
    """Allocate every batch, first in strict window and then in relaxed window."""
    for batch in sorted_batches:
        _allocate_batch(
            batch,
            total_days,
            state,
            use_relaxed_window=False,
        )
        if batch.remaining_sheet_count <= 0:
            continue
        _allocate_batch(
            batch,
            total_days,
            state,
            use_relaxed_window=True,
        )


def _apply_standard_post_processing(
    total_days: int,
    state: ScheduleState,
) -> None:
    """Shape the raw schedule without any rest-day preference.

    Priority here is still production first:
    1. absorb short tails into earlier active days,
    2. rebalance overloaded days when a later white shift can absorb them,
    3. fill 12h/24h targets from later work when legal,
    4. merge very small leftover tails backward to reduce working days.
    """
    _compact_short_tail_days(state)
    _rebalance_night_shift_days(total_days, state)
    _fill_full_shift_days_from_later(total_days, state)
    _merge_tiny_tail_days_backward(total_days, state)


def _apply_rest_day_post_processing(
    total_days: int,
    state: ScheduleState,
    rest_days: Set[int],
) -> None:
    """Apply weekly rest preference after the base schedule is already feasible.

    The intent is to keep this as a second-order optimization:
    - first try to empty preferred rest days completely,
    - then try to give each week one full day off,
    - if that is impossible, fall back to two half-rest days,
    - finally normalize preferred rest days so they are either 0h or close to 12h,
      and never re-merge tiny tails back into the preferred rest-day set.
    """
    if not rest_days:
        return

    _free_preferred_rest_days(total_days, state, rest_days)
    _ensure_weekly_rest_days(total_days, state, rest_days)
    _normalize_rest_day_to_half_shift(total_days, state, rest_days)
    _merge_tiny_tail_days_backward(total_days, state, rest_days)


def _build_schedule_output(
    state: ScheduleState,
) -> Dict[int, List[ScheduledTaskSlice]]:
    """Convert mutable state into the stable API output shape."""
    return {
        day: [state.day_task_map[day][task_index] for task_index in sorted(state.day_task_map[day].keys())]
        for day in sorted(state.day_task_map.keys())
    }


def schedule_coil_tasks(
    batches: List[UncoilingBatch],
    total_days: int,
    rest_days: Optional[List[int]] = None,
) -> Tuple[Dict[int, List[ScheduledTaskSlice]], List[UncoilingBatch]]:
    """Main scheduling entry.

    High-level priority order:
    1. satisfy as much demand as possible inside each task's own legal window,
    2. improve day shape toward 12h / 24h and reduce tiny tails,
    3. only after that, try to preserve preferred weekly rest days.
    """
    if not batches:
        return {}, []

    sorted_batches = sorted(
        (_clone_uncoiling_batch(batch) for batch in batches),
        key=lambda batch: _batch_window_tightness_key(batch, use_relaxed_window=False),
    )

    state = ScheduleState()
    normalized_rest_days = _normalize_rest_days(total_days, rest_days)

    _schedule_batches_into_state(sorted_batches, total_days, state)
    _apply_standard_post_processing(total_days, state)
    _apply_rest_day_post_processing(total_days, state, normalized_rest_days)

    return (_build_schedule_output(state), sorted_batches)


def format_time_display(minutes: float) -> str:
    rounded_minutes = round(minutes, 2)
    whole_days = int(rounded_minutes // MAX_DAILY_MINUTES)
    remaining_minutes = round(rounded_minutes - whole_days * MAX_DAILY_MINUTES, 2)
    whole_hours = int(remaining_minutes // 60)
    final_minutes = round(remaining_minutes - whole_hours * 60, 2)

    def _format_minutes(value: float) -> str:
        if abs(value - round(value)) <= 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    parts: List[str] = []
    if whole_days > 0:
        parts.append(f"{whole_days} 天")
    if whole_hours > 0:
        parts.append(f"{whole_hours} 小时")
    if final_minutes > 1e-9 or not parts:
        parts.append(f"{_format_minutes(final_minutes)} 分钟")
    return " ".join(parts)


def build_coil_spec_summary(spec_key: Tuple[Any, float, float, float], values: Dict[str, float]) -> Dict[str, Any]:
    return {
        "spec_prefix": spec_key[0],
        "coil_length": spec_key[1],
        "coil_width": spec_key[2],
        "coil_thickness": spec_key[3],
        "sheet_count": values["sheets"],
        "total_weight_tons": round(values["weight"], 4),
    }


def aggregate_task_slices_by_work_order(task_slices: List[ScheduledTaskSlice]) -> Dict[str, Dict[str, Any]]:
    work_order_summary: Dict[str, Dict[str, Any]] = {}
    for task_slice in task_slices:
        source = task_slice.task
        entry = work_order_summary.setdefault(
            source.work_order,
            {"weight": 0.0, "sheets": 0, "time": 0.0, "specs": {}},
        )
        entry["weight"] += task_slice.total_weight
        entry["sheets"] += task_slice.sheet_count
        entry["time"] += task_slice.total_time

        spec_key = (
            source.spec_prefix,
            source.coil_length,
            source.coil_width,
            source.coil_thickness,
        )
        spec_entry = entry["specs"].setdefault(spec_key, {"sheets": 0, "weight": 0.0})
        spec_entry["sheets"] += task_slice.sheet_count
        spec_entry["weight"] += task_slice.total_weight

    return work_order_summary


def build_daily_uncoiling_plan(
    output_day: int,
    task_slices: List[ScheduledTaskSlice],
) -> Tuple[Dict[str, Any], float, int, float]:
    work_order_summary = aggregate_task_slices_by_work_order(task_slices)
    daily_weight = round(sum(values["weight"] for values in work_order_summary.values()), 4)
    daily_sheets = sum(values["sheets"] for values in work_order_summary.values())
    daily_time = round(sum(values["time"] for values in work_order_summary.values()), 2)

    work_orders = []
    for work_order, values in sorted(work_order_summary.items()):
        work_orders.append(
            {
                "work_order": work_order,
                "total_weight_tons": round(values["weight"], 4),
                "sheet_count": values["sheets"],
                "estimated_time_minutes": round(values["time"], 2),
                "specs": [
                    build_coil_spec_summary(spec_key, spec_values)
                    for spec_key, spec_values in sorted(values["specs"].items())
                ],
            }
        )

    plan = {
        "day": output_day,
        "work_orders": work_orders,
        "daily_total_weight_tons": daily_weight,
        "daily_total_sheets": daily_sheets,
        "daily_total_time_minutes": daily_time,
    }
    return plan, daily_weight, daily_sheets, daily_time


def build_pre_stock_requirements(
    scheduled_batches: List[UncoilingBatch],
) -> List[Dict[str, Any]]:
    day_spec_map: Dict[int, Dict[Tuple[Any, ...], Dict[str, Any]]] = {}

    for batch in scheduled_batches:
        if batch.remaining_sheet_count <= 0:
            continue

        spec_key = (batch.spec_prefix, batch.coil_length, batch.coil_width, batch.coil_thickness)
        for task in batch.tasks:
            remaining = batch.remaining_by_task.get(task.input_index, 0)
            if remaining <= 0:
                continue

            day = task.process_day
            spec_map = day_spec_map.setdefault(day, {})
            entry = spec_map.get(spec_key)
            if entry is None:
                entry = {
                    "sheets": 0,
                    "time": 0.0,
                    "weight_per_sheet": task.weight_per_sheet,
                    "work_orders": set(),
                    "part_ids": set(),
                }
                spec_map[spec_key] = entry

            entry["sheets"] += remaining
            entry["time"] += batch.time_per_sheet * remaining
            entry["work_orders"].add(task.work_order)
            if task.part_id:
                entry["part_ids"].add(task.part_id)

    result: List[Dict[str, Any]] = []
    for day in sorted(day_spec_map.keys()):
        specs: List[Dict[str, Any]] = []
        daily_sheets = 0
        daily_weight = 0.0
        daily_time = 0.0

        for spec_key, entry in sorted(day_spec_map[day].items()):
            weight = round(entry["weight_per_sheet"] * entry["sheets"], 4)
            specs.append(
                {
                    "spec_prefix": spec_key[0],
                    "coil_length": spec_key[1],
                    "coil_width": spec_key[2],
                    "coil_thickness": spec_key[3],
                    "shortage_sheets": entry["sheets"],
                    "shortage_weight_tons": weight,
                    "shortage_time_minutes": round(entry["time"], 2),
                    "work_orders": sorted(entry["work_orders"]),
                    "part_ids": sorted(entry["part_ids"]),
                }
            )
            daily_sheets += entry["sheets"]
            daily_weight += weight
            daily_time += entry["time"]

        result.append(
            {
                "day": day,
                "specs": specs,
                "daily_shortage_sheets": daily_sheets,
                "daily_shortage_weight_tons": round(daily_weight, 4),
                "daily_shortage_time_minutes": round(daily_time, 2),
            }
        )

    return result
