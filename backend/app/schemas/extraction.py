from pydantic import BaseModel, ConfigDict, Field, field_validator


class PdfParseResponse(BaseModel):
    text: str
    warnings: list[str] = Field(default_factory=list)


class ExtractionRequest(BaseModel):
    text: str


# camelCase on the wire — matches the AI's JSON output format and what the
# frontend's extraction flow already consumes.
class OrderLineData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    position_description: str = Field(default="", alias="positionDescription")
    unit_price: float = Field(default=0, alias="unitPrice")
    amount: float = 0
    unit: str = ""
    total_price: float = Field(default=0, alias="totalPrice")

    @field_validator("position_description", "unit", mode="before")
    @classmethod
    def empty_optional_text(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("unit_price", "amount", "total_price", mode="before")
    @classmethod
    def empty_optional_number(cls, value: object) -> object:
        return 0 if value is None else value


class ExtractedVendorData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str = ""
    vendor_name: str = Field(default="", alias="vendorName")
    vat_id: str = Field(default="", alias="vatId")
    department: str = ""
    order_lines: list[OrderLineData] = Field(default_factory=list, alias="orderLines")
    alternative_order_lines: list[OrderLineData] = Field(
        default_factory=list, alias="alternativeOrderLines"
    )
    total_cost: float = Field(default=0, alias="totalCost")
    # Only filled by server-side verification of the printed quote, never by the model.
    tax_rate_percent: float | None = Field(default=None, alias="taxRatePercent")
    commodity_group_id: int | None = Field(default=None, alias="commodityGroupId")
    commodity_group_name: str | None = Field(default=None, alias="commodityGroupName")

    @field_validator("title", "vendor_name", "vat_id", "department", mode="before")
    @classmethod
    def empty_optional_text(cls, value: object) -> object:
        return "" if value is None else value

    @field_validator("order_lines", "alternative_order_lines", mode="before")
    @classmethod
    def empty_optional_lines(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("total_cost", mode="before")
    @classmethod
    def empty_optional_total(cls, value: object) -> object:
        return 0 if value is None else value


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    success: bool
    data: ExtractedVendorData | None = None
    error: str | None = None
    missing_fields: list[str] | None = Field(default=None, alias="missingFields")
    warnings: list[str] = Field(default_factory=list)
