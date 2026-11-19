"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  Calendar,
  Table as TableIcon,
} from "lucide-react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ApiError, adminAnalyticsApi } from "@/lib/api/client";
import type {
  AnalyticsSummaryResponse,
  AnalyticsTimeseriesResponse,
  InventoryHealthResponse,
  OrderStatus,
  TopProductSortBy,
  TopProductsResponse,
} from "@/lib/api/types";
import { formatCents } from "@/lib/format";
import {
  MAX_ANALYTICS_SPAN_DAYS,
  computeStockBadgeState,
  formatUtcDateInput,
  resolveErrorMessage,
  toUtcRangeEndIso,
  toUtcRangeStartIso,
  utcDateShift,
  utcDaysBetween,
} from "@/components/admin/admin-helpers";
import {
  AdminPageHeader,
  DataTableShell,
  KpiCard,
} from "@/components/admin/admin-primitives";
import { EmptyState, ErrorState, StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Pagination } from "@/components/ui/pagination";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export { AdminProductsView as AdminProductsStarterView } from "./admin-products-view";

type PresetKey = "7d" | "30d" | "90d" | "365d" | "custom";

const PRESET_OPTIONS: Array<{ key: Exclude<PresetKey, "custom">; label: string; days: number }> = [
  { key: "7d", label: "Last 7 days", days: 7 },
  { key: "30d", label: "Last 30 days", days: 30 },
  { key: "90d", label: "Last 90 days", days: 90 },
  { key: "365d", label: "Last 365 days", days: 365 },
];

const CANONICAL_ORDER_STATUSES: OrderStatus[] = [
  "PENDING_PAYMENT",
  "CONFIRMED",
  "PROCESSING",
  "SHIPPED",
  "COMPLETED",
  "CANCELLED",
  "REFUNDED",
];

const HEALTH_PAGE_SIZE = 10;

function computePresetRange(days: number): { from: string; to: string } {
  const todayUtc = formatUtcDateInput(new Date());
  return {
    from: utcDateShift(todayUtc, -(days - 1)),
    to: todayUtc,
  };
}

function validateUtcDateRange(from: string, to: string): string | null {
  if (!from || !to) {
    return "Both From and To UTC dates are required.";
  }
  const diffDays = utcDaysBetween(from, to);
  if (diffDays === null) {
    return "Enter valid YYYY-MM-DD UTC dates.";
  }
  if (diffDays < 0) {
    return "Start date must be before or equal to end date.";
  }
  if (diffDays > MAX_ANALYTICS_SPAN_DAYS) {
    return "Date range cannot exceed 366 days";
  }
  return null;
}

interface ChartTooltipEntry {
  dataKey?: string;
  value?: number;
  payload?: {
    date?: string;
    net_revenue_cents?: number;
    paid_orders_count?: number;
  };
}

function AnalyticsChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: ChartTooltipEntry[];
  label?: string | number;
}) {
  if (!active || !payload || payload.length === 0) {
    return null;
  }
  const point = payload[0]?.payload;
  const dateLabel = point?.date ?? String(label ?? "");
  const netRevenueCents = point?.net_revenue_cents ?? 0;
  const paidOrdersCount = point?.paid_orders_count ?? 0;

  return (
    <div className="rounded-md border border-border bg-surface p-3 text-xs shadow-md">
      <p className="font-mono font-semibold text-foreground">{dateLabel} UTC</p>
      <div className="mt-1.5 space-y-1 tabular-nums">
        <p className="flex items-center justify-between gap-4 text-foreground">
          <span className="text-muted-foreground">Net Revenue:</span>
          <span className="font-semibold">{formatCents(netRevenueCents)}</span>
        </p>
        <p className="flex items-center justify-between gap-4 text-foreground">
          <span className="text-muted-foreground">Paid Orders:</span>
          <span className="font-semibold">{paidOrdersCount}</span>
        </p>
      </div>
    </div>
  );
}

export function AdminDashboardView() {
  const defaultRange = React.useMemo(() => computePresetRange(30), []);

  const [preset, setPreset] = React.useState<PresetKey>("30d");
  const [appliedRange, setAppliedRange] = React.useState<{ from: string; to: string }>(
    defaultRange
  );
  const [customFrom, setCustomFrom] = React.useState(defaultRange.from);
  const [customTo, setCustomTo] = React.useState(defaultRange.to);
  const [dateRangeError, setDateRangeError] = React.useState<string | null>(null);

  // Core analytics (summary + timeseries)
  const [summary, setSummary] = React.useState<AnalyticsSummaryResponse | null>(null);
  const [timeseries, setTimeseries] = React.useState<AnalyticsTimeseriesResponse | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = React.useState(true);
  const [analyticsError, setAnalyticsError] = React.useState<string | null>(null);
  const [analyticsNonce, setAnalyticsNonce] = React.useState(0);

  // Chart options
  const [chartTab, setChartTab] = React.useState<"chart" | "table">("chart");
  const [showOrdersOverlay, setShowOrdersOverlay] = React.useState(true);

  // Top products
  const [topSortBy, setTopSortBy] = React.useState<TopProductSortBy>("revenue");
  const [topLimit, setTopLimit] = React.useState<number>(10);
  const [topProducts, setTopProducts] = React.useState<TopProductsResponse | null>(null);
  const [topLoading, setTopLoading] = React.useState(true);
  const [topError, setTopError] = React.useState<string | null>(null);
  const [topNonce, setTopNonce] = React.useState(0);

  // Inventory health
  const [healthPage, setHealthPage] = React.useState(1);
  const [inventoryHealth, setInventoryHealth] = React.useState<InventoryHealthResponse | null>(
    null
  );
  const [healthLoading, setHealthLoading] = React.useState(true);
  const [healthError, setHealthError] = React.useState<string | null>(null);
  const [healthNonce, setHealthNonce] = React.useState(0);

  // Fetch Summary & Timeseries for appliedRange
  React.useEffect(() => {
    const controller = new AbortController();
    setAnalyticsLoading(true);
    setAnalyticsError(null);

    Promise.all([
      adminAnalyticsApi.getSummary(
        { from: appliedRange.from, to: appliedRange.to },
        controller.signal
      ),
      adminAnalyticsApi.getTimeseries(
        { from: appliedRange.from, to: appliedRange.to },
        controller.signal
      ),
    ])
      .then(([summaryRes, timeseriesRes]) => {
        setSummary(summaryRes);
        setTimeseries(timeseriesRes);
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError && err.status === 400 && err.code === "INVALID_DATE_RANGE") {
          setDateRangeError(err.message || "Date range cannot exceed 366 days");
        }
        setAnalyticsError(resolveErrorMessage(err, "Unable to load sales analytics."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setAnalyticsLoading(false);
        }
      });

    return () => controller.abort();
  }, [appliedRange.from, appliedRange.to, analyticsNonce]);

  // Fetch Top Products independently when range, sort_by, or limit changes
  React.useEffect(() => {
    const controller = new AbortController();
    setTopLoading(true);
    setTopError(null);

    adminAnalyticsApi
      .getTopProducts(
        {
          from: appliedRange.from,
          to: appliedRange.to,
          sort_by: topSortBy,
          limit: topLimit,
        },
        controller.signal
      )
      .then((res) => setTopProducts(res))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (err instanceof ApiError && err.status === 400 && err.code === "INVALID_DATE_RANGE") {
          setDateRangeError(err.message || "Date range cannot exceed 366 days");
        }
        setTopError(resolveErrorMessage(err, "Unable to load top products."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setTopLoading(false);
        }
      });

    return () => controller.abort();
  }, [appliedRange.from, appliedRange.to, topSortBy, topLimit, topNonce]);

  // Fetch Inventory Health independently
  React.useEffect(() => {
    const controller = new AbortController();
    setHealthLoading(true);
    setHealthError(null);

    adminAnalyticsApi
      .getInventoryHealth(
        { page: healthPage, page_size: HEALTH_PAGE_SIZE },
        controller.signal
      )
      .then((res) => setInventoryHealth(res))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        setHealthError(resolveErrorMessage(err, "Unable to load inventory health."));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setHealthLoading(false);
        }
      });

    return () => controller.abort();
  }, [healthPage, healthNonce]);

  const handleSelectPreset = (nextPreset: PresetKey) => {
    setPreset(nextPreset);
    setDateRangeError(null);
    if (nextPreset !== "custom") {
      const option = PRESET_OPTIONS.find((item) => item.key === nextPreset);
      if (option) {
        const range = computePresetRange(option.days);
        setCustomFrom(range.from);
        setCustomTo(range.to);
        setAppliedRange(range);
      }
    }
  };

  const handleApplyCustomRange = (e: React.FormEvent) => {
    e.preventDefault();
    const validationErr = validateUtcDateRange(customFrom, customTo);
    if (validationErr) {
      setDateRangeError(validationErr);
      return;
    }
    setDateRangeError(null);
    setPreset("custom");
    setAppliedRange({ from: customFrom, to: customTo });
  };

  const fromIso = toUtcRangeStartIso(appliedRange.from) ?? "";
  const toIso = toUtcRangeEndIso(appliedRange.to) ?? "";
  const points = timeseries?.points ?? [];
  const hasAnySales = points.some(
    (pt) => pt.net_revenue_cents > 0 || pt.paid_orders_count > 0
  );

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Sales & Operations Dashboard"
        description="UTC-aligned revenue metrics, order pipeline triage, and active SKU health."
        badge={<Badge variant="outline">UTC Timezone</Badge>}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" asChild>
              <Link href="/admin/orders">Orders Workbench</Link>
            </Button>
            <Button variant="outline" size="sm" asChild>
              <Link href="/admin/inventory">Inventory Control</Link>
            </Button>
          </div>
        }
      />

      {/* Bounded UTC Date Range Picker */}
      <Card className="shadow-xs">
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="UTC Date Range Presets">
              {PRESET_OPTIONS.map((opt) => (
                <Button
                  key={opt.key}
                  type="button"
                  size="sm"
                  variant={preset === opt.key ? "default" : "outline"}
                  aria-pressed={preset === opt.key}
                  onClick={() => handleSelectPreset(opt.key)}
                >
                  {opt.label}
                </Button>
              ))}
              <Button
                type="button"
                size="sm"
                variant={preset === "custom" ? "default" : "outline"}
                aria-pressed={preset === "custom"}
                onClick={() => handleSelectPreset("custom")}
              >
                <Calendar className="h-3.5 w-3.5" aria-hidden="true" />
                Custom UTC Range
              </Button>
            </div>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>Active window:</span>
              <span className="rounded bg-muted px-2 py-0.5 font-mono text-foreground">
                {appliedRange.from} → {appliedRange.to} UTC
              </span>
            </div>
          </div>

          {preset === "custom" ? (
            <form
              onSubmit={handleApplyCustomRange}
              className="flex flex-wrap items-end gap-3 border-t border-border pt-3"
            >
              <div className="space-y-1">
                <Label htmlFor="analytics-from-date" className="text-xs">
                  From (UTC)
                </Label>
                <Input
                  id="analytics-from-date"
                  type="date"
                  value={customFrom}
                  onChange={(e) => {
                    setCustomFrom(e.target.value);
                    setDateRangeError(null);
                  }}
                  hasError={Boolean(dateRangeError)}
                  className="w-40"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="analytics-to-date" className="text-xs">
                  To (UTC)
                </Label>
                <Input
                  id="analytics-to-date"
                  type="date"
                  value={customTo}
                  onChange={(e) => {
                    setCustomTo(e.target.value);
                    setDateRangeError(null);
                  }}
                  hasError={Boolean(dateRangeError)}
                  className="w-40"
                />
              </div>
              <Button type="submit" size="sm" variant="accent">
                Apply Range
              </Button>
            </form>
          ) : null}

          {dateRangeError ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{dateRangeError}</AlertDescription>
            </Alert>
          ) : null}
        </CardContent>
      </Card>

      {analyticsError && !dateRangeError ? (
        <ErrorState
          title="Unable to load sales analytics"
          message={analyticsError}
          onRetry={() => {
            setAnalyticsNonce((n) => n + 1);
            setTopNonce((n) => n + 1);
          }}
        />
      ) : null}

      {/* 4 KPI Summary Cards */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard
          title="Net Revenue"
          value={summary ? formatCents(summary.net_revenue_cents) : "$0.00"}
          subtitle="Gross minus refunds"
          isLoading={analyticsLoading}
        />
        <KpiCard
          title="Gross Revenue"
          value={summary ? formatCents(summary.gross_revenue_cents) : "$0.00"}
          subtitle={
            summary && summary.refunded_revenue_cents > 0 ? (
              <span className="font-medium text-destructive">
                Refunded: -{formatCents(summary.refunded_revenue_cents)}
              </span>
            ) : (
              "Refunded: $0.00"
            )
          }
          isLoading={analyticsLoading}
        />
        <KpiCard
          title="Paid Orders"
          value={summary ? summary.paid_orders_count : 0}
          subtitle="Recognized PAID orders in UTC window"
          isLoading={analyticsLoading}
        />
        <KpiCard
          title="Average Order Value"
          value={summary ? formatCents(summary.average_order_value_cents) : "$0.00"}
          subtitle="Net revenue / paid orders"
          isLoading={analyticsLoading}
        />
      </div>

      {/* Interactive Order Status Breakdown Pills */}
      <Card className="shadow-xs">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Orders by Status (Placed in UTC Window)</CardTitle>
          <CardDescription>
            Select any status pill to open the Orders Workbench filtered to that status and window.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {analyticsLoading ? (
            <div className="flex flex-wrap gap-2">
              {CANONICAL_ORDER_STATUSES.map((st) => (
                <Skeleton key={st} className="h-9 w-36" />
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap gap-2.5">
              {CANONICAL_ORDER_STATUSES.map((status) => {
                const count = summary?.orders_by_status?.[status] ?? 0;
                const href = `/admin/orders?status=${encodeURIComponent(
                  status
                )}&from_ts=${encodeURIComponent(fromIso)}&to_ts=${encodeURIComponent(toIso)}`;
                return (
                  <Link
                    key={status}
                    href={href}
                    className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-sm transition-colors hover:border-accent hover:bg-accent-subtle/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <StatusBadge domain="order" value={status} />
                    <span className="font-semibold text-foreground tabular-nums">{count}</span>
                  </Link>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Daily Net Revenue & Paid Orders Chart + Accessible Data Table */}
      <Card className="shadow-xs">
        <Tabs value={chartTab} onValueChange={(v) => setChartTab(v as "chart" | "table")}>
          <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>Daily Net Revenue & Paid Orders</CardTitle>
              <CardDescription>
                Continuous UTC daily metrics ({points.length} day{points.length === 1 ? "" : "s"}).
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              {chartTab === "chart" ? (
                <label className="flex cursor-pointer items-center gap-2 text-xs font-medium text-foreground">
                  <Checkbox
                    checked={showOrdersOverlay}
                    onCheckedChange={(checked) => setShowOrdersOverlay(Boolean(checked))}
                  />
                  <span>Show Paid Orders Line</span>
                </label>
              ) : null}
              <TabsList aria-label="Analytics view mode">
                <TabsTrigger value="chart" className="gap-1.5">
                  <BarChart3 className="h-3.5 w-3.5" aria-hidden="true" />
                  Chart
                </TabsTrigger>
                <TabsTrigger value="table" className="gap-1.5">
                  <TableIcon className="h-3.5 w-3.5" aria-hidden="true" />
                  Data Table
                </TabsTrigger>
              </TabsList>
            </div>
          </CardHeader>

          <CardContent>
            {analyticsLoading ? (
              <Skeleton className="h-[280px] w-full" />
            ) : points.length === 0 ? (
              <EmptyState
                title="No daily points in range"
                description="Select a valid UTC range to inspect daily sales."
              />
            ) : (
              <>
                <TabsContent value="chart" className="mt-0">
                  {!hasAnySales ? (
                    <p className="mb-3 text-xs text-muted-foreground">
                      Zero paid sales recorded in this UTC window — showing baseline.
                    </p>
                  ) : null}
                  <div className="min-h-[280px] w-full" aria-label="Daily Net Revenue and Paid Orders chart">
                    <ResponsiveContainer
                      width="100%"
                      height={300}
                      initialDimension={{ width: 640, height: 300 }}
                    >
                      <ComposedChart
                        data={points}
                        margin={{ top: 10, right: 16, left: 4, bottom: 8 }}
                      >
                        <CartesianGrid
                          stroke="var(--chart-grid)"
                          strokeDasharray="3 3"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="date"
                          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                          tickFormatter={(val: string) =>
                            points.length > 45 ? val.slice(5) : val
                          }
                          minTickGap={24}
                        />
                        <YAxis
                          yAxisId="revenue"
                          tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                          tickFormatter={(cents: number) =>
                            `$${Math.round(cents / 100).toLocaleString("en-US")}`
                          }
                          width={64}
                        />
                        {showOrdersOverlay ? (
                          <YAxis
                            yAxisId="orders"
                            orientation="right"
                            allowDecimals={false}
                            tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                            width={40}
                          />
                        ) : null}
                        <Tooltip content={<AnalyticsChartTooltip />} />
                        <Bar
                          yAxisId="revenue"
                          dataKey="net_revenue_cents"
                          name="Net Revenue"
                          fill="var(--chart-net-revenue)"
                          radius={[4, 4, 0, 0]}
                          maxBarSize={36}
                        />
                        {showOrdersOverlay ? (
                          <Line
                            yAxisId="orders"
                            type="monotone"
                            dataKey="paid_orders_count"
                            name="Paid Orders"
                            stroke="var(--chart-orders-count)"
                            strokeWidth={2}
                            dot={{ r: 2 }}
                            activeDot={{ r: 4 }}
                          />
                        ) : null}
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </TabsContent>

                <TabsContent value="table" className="mt-0">
                  <div className="max-h-[340px] overflow-y-auto rounded-md border border-border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Date (UTC)</TableHead>
                          <TableHead className="text-right">Net Revenue</TableHead>
                          <TableHead className="text-right">Paid Orders</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {points.map((pt) => (
                          <TableRow key={pt.date}>
                            <TableCell className="font-mono text-xs">{pt.date}</TableCell>
                            <TableCell className="text-right font-medium tabular-nums">
                              {formatCents(pt.net_revenue_cents)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {pt.paid_orders_count}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </TabsContent>
              </>
            )}
          </CardContent>
        </Tabs>
      </Card>

      {/* Bottom Split Grid: Top Products & Inventory Health */}
      <div className="grid gap-6 xl:grid-cols-2">
        {/* Top Products */}
        <DataTableShell
          title="Top Products & Variants"
          description="Ranked across PAID orders in the selected UTC range."
          headerActions={
            <div className="flex flex-wrap items-center gap-2">
              <div className="inline-flex rounded-md border border-border bg-muted p-0.5">
                <Button
                  type="button"
                  size="sm"
                  variant={topSortBy === "revenue" ? "default" : "ghost"}
                  className="h-7 px-2.5 text-xs"
                  onClick={() => setTopSortBy("revenue")}
                >
                  Revenue
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant={topSortBy === "units" ? "default" : "ghost"}
                  className="h-7 px-2.5 text-xs"
                  onClick={() => setTopSortBy("units")}
                >
                  Units Sold
                </Button>
              </div>

              <Select
                value={String(topLimit)}
                onValueChange={(val) => setTopLimit(Number.parseInt(val, 10))}
              >
                <SelectTrigger className="h-8 w-[88px] text-xs" aria-label="Top products limit">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="10">Top 10</SelectItem>
                  <SelectItem value="25">Top 25</SelectItem>
                  <SelectItem value="50">Top 50</SelectItem>
                </SelectContent>
              </Select>
            </div>
          }
          isLoading={topLoading}
          error={topError}
          onRetry={() => setTopNonce((n) => n + 1)}
          isEmpty={!topProducts || topProducts.items.length === 0}
          emptyTitle="No paid sales in this UTC range"
          emptyDescription="Top-performing SKUs will appear once paid orders fall within this date window."
        >
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product / Variant</TableHead>
                <TableHead>SKU</TableHead>
                <TableHead className="text-right">Units Sold</TableHead>
                <TableHead className="text-right">Revenue</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(topProducts?.items ?? []).map((item) => (
                <TableRow key={item.variant_id}>
                  <TableCell>
                    <Link
                      href={`/admin/products/${item.product_id}`}
                      className="font-medium text-foreground hover:text-accent hover:underline"
                    >
                      {item.product_title_snapshot}
                    </Link>
                    <p className="text-xs text-muted-foreground">
                      {item.variant_title_snapshot}
                    </p>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {item.sku_snapshot}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{item.units_sold}</TableCell>
                  <TableCell className="text-right font-semibold tabular-nums">
                    {formatCents(item.revenue_cents)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DataTableShell>

        {/* Inventory Health Alerts */}
        <Card className="overflow-hidden shadow-xs">
          <div className="space-y-3 border-b border-border px-4 py-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-base font-semibold text-foreground">
                  Inventory Health Alerts
                </h2>
                <p className="text-xs text-muted-foreground">
                  Active SKUs sorted by lowest available stock first.
                </p>
              </div>
              <Button variant="outline" size="sm" asChild>
                <Link href="/admin/inventory">
                  All Inventory
                  <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
                </Link>
              </Button>
            </div>

            {inventoryHealth ? (
              <div className="flex flex-wrap items-center gap-2 text-xs tabular-nums">
                <Badge variant="outline">
                  Active SKUs: {inventoryHealth.summary.active_skus_count}
                </Badge>
                <Badge
                  variant={
                    inventoryHealth.summary.out_of_stock_count > 0
                      ? "destructive"
                      : "secondary"
                  }
                >
                  Out of Stock: {inventoryHealth.summary.out_of_stock_count}
                </Badge>
                <Badge
                  variant={
                    inventoryHealth.summary.low_stock_count > 0 ? "warning" : "secondary"
                  }
                >
                  Low Stock: {inventoryHealth.summary.low_stock_count}
                </Badge>
                <Badge variant="outline">
                  Reserved Units: {inventoryHealth.summary.total_reserved_units}
                </Badge>
              </div>
            ) : null}
          </div>

          {healthLoading ? (
            <div className="space-y-2.5 p-4" role="status" aria-label="Loading inventory health">
              {[1, 2, 3, 4].map((row) => (
                <Skeleton key={row} className="h-10 w-full" />
              ))}
            </div>
          ) : healthError ? (
            <div className="p-4">
              <ErrorState
                title="Unable to load inventory health"
                message={healthError}
                onRetry={() => setHealthNonce((n) => n + 1)}
              />
            </div>
          ) : !inventoryHealth || inventoryHealth.alerts.items.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="All active SKUs healthy"
                description="No active SKUs are currently out of stock or below threshold."
                className="border-none py-8"
              />
            </div>
          ) : (
            <div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>SKU</TableHead>
                    <TableHead>Product / Variant</TableHead>
                    <TableHead className="text-right">Avail / Thresh</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Action</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {inventoryHealth.alerts.items.map((alert) => {
                    const stockState = computeStockBadgeState(
                      alert.in_stock,
                      alert.low_stock
                    );
                    return (
                      <TableRow key={alert.variant_id}>
                        <TableCell className="font-mono text-xs">{alert.sku}</TableCell>
                        <TableCell>
                          <p className="font-medium text-foreground">{alert.product_title}</p>
                          <p className="text-xs text-muted-foreground">
                            {alert.variant_title}
                          </p>
                        </TableCell>
                        <TableCell className="text-right text-xs tabular-nums">
                          <span className="font-semibold">{alert.available_quantity}</span>
                          <span className="text-muted-foreground">
                            {" "}
                            / {alert.low_stock_threshold}
                          </span>
                        </TableCell>
                        <TableCell>
                          <StatusBadge
                            domain="stock"
                            value={stockState}
                            availableQuantity={alert.available_quantity}
                          />
                        </TableCell>
                        <TableCell className="text-right">
                          <Button variant="outline" size="sm" asChild>
                            <Link
                              href={`/admin/inventory?sku=${encodeURIComponent(alert.sku)}`}
                            >
                              Adjust Stock
                            </Link>
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>

              {inventoryHealth.alerts.total > 0 ? (
                <div className="px-4 pb-4">
                  <Pagination
                    page={inventoryHealth.alerts.page}
                    pageSize={inventoryHealth.alerts.page_size}
                    total={inventoryHealth.alerts.total}
                    hasNext={inventoryHealth.alerts.has_next}
                    onPageChange={(nextPage) => setHealthPage(nextPage)}
                    itemLabel="alerts"
                  />
                </div>
              ) : null}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
