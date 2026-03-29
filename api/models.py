# -*- coding: utf-8 -*-
"""API data models."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SegmentConfig(BaseModel):
    segment_id: str = Field(..., description="Segment identifier")
    process_codes: List[str] = Field(..., description="Process code list")
    children: List["SegmentConfig"] = Field(default_factory=list, description="Child segments")


class ProductConfig(BaseModel):
    product_id: str = Field(..., description="Product identifier")
    product_type: str = Field(default="standard", description="Product type")
    work_order: str = Field(default="", description="Work order")
    process_codes: List[str] = Field(default_factory=list, description="Post-assembly process codes")
    segments: List[SegmentConfig] = Field(..., description="All segments for the product")


SegmentConfig.model_rebuild()


class DeviceAdjustment(BaseModel):
    device_name: str = Field(..., description="Device type name")
    start_time: float = Field(..., description="Adjustment start time in minutes")
    end_time: float = Field(..., description="Adjustment end time in minutes")
    adjusted_time: Optional[float] = Field(None, description="Adjusted effective work time in minutes")
    count: Optional[int] = Field(None, description="Adjusted device count")


class PipeSimulationRequest(BaseModel):
    config_type: str = Field(..., description="Configuration type")
    products_config: List[ProductConfig] = Field(..., description="Product configuration list")
    simulation_duration: float = Field(..., description="Simulation duration in minutes")
    daily_work_time: float = Field(default=480.0, description="Daily working time in minutes")
    device_adjustments: Optional[List[DeviceAdjustment]] = Field(
        None, description="Dynamic device adjustment list"
    )


class LineUtilization(BaseModel):
    line_name: str = Field(..., description="Line name")
    overall_utilization: float = Field(..., description="Overall utilization")
    process_utilization: Optional[Dict[str, float]] = Field(None, description="Utilization by process")


class DailySegmentInfo(BaseModel):
    day: int = Field(..., description="Day number")
    device_utilization: Dict[str, float] = Field(default_factory=dict, description="Device utilization map")


class PipeAnalysisResponse(BaseModel):
    simulation_summary: Dict[str, Any] = Field(..., description="Simulation summary")
    total_device_utilization: Dict[str, float] = Field(..., description="Overall device utilization")
    utilization_analysis: List[LineUtilization] = Field(..., description="Line utilization analysis")
    daily_segments: List[DailySegmentInfo] = Field(..., description="Daily segments")
    simulation_details: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Detailed simulation data"
    )


class ContainerSimulationRequest(BaseModel):
    config_type: str = Field(default="DFICNB", description="Configuration type")
    products_config: List[ProductConfig] = Field(..., description="Product configuration list")
    simulation_duration: float = Field(..., description="Simulation duration in minutes")
    daily_work_time: float = Field(default=480.0, description="Daily working time in minutes")


class DeviceWorkTime(BaseModel):
    device_name: str = Field(..., description="Device name")
    work_time: float = Field(..., description="Work time in minutes")
    work_order_times: List["WorkOrderWorkTime"] = Field(
        default_factory=list, description="Work time grouped by work order"
    )


class WorkOrderWorkTime(BaseModel):
    work_order: str = Field(..., description="Work order")
    work_time: float = Field(..., description="Work time in minutes")


DeviceWorkTime.model_rebuild()


class TeamWorkTime(BaseModel):
    name: str = Field(..., description="Team or line name")
    work_time: float = Field(..., description="Work time in minutes")
    work_order_times: List[WorkOrderWorkTime] = Field(
        default_factory=list, description="Work time grouped by work order"
    )
    devices: List[DeviceWorkTime] = Field(..., description="Per-device work time")


class ContainerAnalysisResponse(BaseModel):
    simulation_duration: float = Field(..., description="Simulation duration in minutes")
    teams: List[TeamWorkTime] = Field(..., description="Per-team work time")


class RecommendationType(str, Enum):
    EXTEND_WORK_HOURS = "extend_work_hours"
    ADD_DEVICES = "add_devices"


class Recommendation(BaseModel):
    device_type: str = Field(..., description="Device type")
    recommendation_type: RecommendationType = Field(..., description="Recommendation type")
    target_days: List[int] = Field(..., description="Target days")
    additional_hours: Optional[float] = Field(None, description="Additional hours")
    additional_devices: Optional[int] = Field(None, description="Additional devices")
    reason: str = Field(..., description="Recommendation reason")


class SimulationComparison(BaseModel):
    baseline_completion_time: float = Field(..., description="Baseline completion time in minutes")
    optimized_completion_time: float = Field(..., description="Optimized completion time in minutes")
    time_reduction_minutes: float = Field(..., description="Reduced time in minutes")
    time_reduction_percentage: float = Field(..., description="Reduced time percentage")


class OptimizationRecommendationResponse(BaseModel):
    recommendations: List[Recommendation] = Field(..., description="Optimization recommendations")
    comparison: SimulationComparison = Field(..., description="Simulation comparison")
    baseline_summary: Dict[str, Any] = Field(..., description="Baseline summary")
    optimized_summary: Dict[str, Any] = Field(..., description="Optimized summary")
    device_adjustments: List[DeviceAdjustment] = Field(..., description="Generated device adjustments")


class CoilPartConfig(BaseModel):
    part_id: str = Field(..., description="Part identifier")
    work_order: str = Field(..., description="Work order")
    manufacturer: Optional[str] = Field(
        None,
        description="Manufacturer; blank means 100% efficiency, configured names use mapped efficiency",
    )
    coil_length: float = Field(..., description="Coil length in mm")
    coil_width: float = Field(..., description="Coil width in mm")
    coil_thickness: float = Field(..., description="Coil thickness in mm")
    sheet_count: int = Field(..., description="Sheet count")
    spec_prefix: Optional[str] = Field(None, description="Specification prefix")


class DailyPartsConfig(BaseModel):
    day: int = Field(..., description="Process day, starting from 1")
    parts: List[CoilPartConfig] = Field(..., description="Parts required for the day")


class CoilUncoilingRequest(BaseModel):
    total_days: int = Field(..., description="Overall scheduling horizon in days")
    rest_days: List[int] = Field(
        default_factory=list,
        description="Preferred rest days within total_days; scheduler will try to use these days last",
    )
    scheduling_window_days: Optional[int] = Field(
        default=None,
        description="Effective preprocessing scheduling window in days; defaults to total_days",
    )
    lead_time_minutes: float = Field(
        default=2880.0,
        description="Required lead time before process day in minutes",
    )
    daily_parts: List[DailyPartsConfig] = Field(..., description="Demand grouped by process day")


class CoilSpecSummary(BaseModel):
    spec_prefix: Optional[str] = Field(None, description="Specification prefix")
    coil_length: float = Field(..., description="Coil length in mm")
    coil_width: float = Field(..., description="Coil width in mm")
    coil_thickness: float = Field(..., description="Coil thickness in mm")
    sheet_count: int = Field(..., description="Sheet count")
    total_weight_tons: float = Field(..., description="Total weight in tons")


class WorkOrderUncoilingSummary(BaseModel):
    work_order: str = Field(..., description="Work order")
    total_weight_tons: float = Field(..., description="Total weight in tons")
    sheet_count: int = Field(..., description="Sheet count")
    estimated_time_minutes: float = Field(..., description="Estimated uncoiling time in minutes")
    specs: List[CoilSpecSummary] = Field(..., description="Per-spec summary for the work order")


class DailyUncoilingPlan(BaseModel):
    day: int = Field(..., description="Scheduling day")
    work_orders: List[WorkOrderUncoilingSummary] = Field(..., description="Daily plan by work order")
    daily_total_weight_tons: float = Field(..., description="Daily total weight in tons")
    daily_total_sheets: int = Field(..., description="Daily total sheets")
    daily_total_time_minutes: float = Field(..., description="Daily total uncoiling time in minutes")


class CoilUncoilingResponse(BaseModel):
    schedule: List[DailyUncoilingPlan] = Field(..., description="Daily uncoiling plan")
    summary: Dict[str, Any] = Field(..., description="Summary information")
