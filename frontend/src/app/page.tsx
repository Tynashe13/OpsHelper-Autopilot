'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
} from 'recharts'
import { apiClient } from '@/lib/api-client'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CardWatermark } from '@/components/ui/card-watermark'
import { Icons } from '@/components/ui/icons'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

// ============================================================================
// Types — mirrors app/schemas/dashboard.py's DashboardSummary
// ============================================================================

type RunStatus = 'running' | 'pending_approval' | 'auto_executed' | 'approved' | 'rejected' | 'failed'

interface AgentStats {
  total: number
  pending_approval: number
  auto_executed: number
  approved: number
  rejected: number
  failed: number
  running: number
}

interface DailyRunCount {
  date: string
  count: number
}

interface RecentRun {
  id: string
  supplier_label: string
  status: RunStatus
  created_at: string
  cost_avoided: number | null
  time_saved_hours: number | null
}

interface SupabaseLiveCounts {
  suppliers: number | null
  disruption_notices: number | null
  inventory_positions: number | null
  shipments: number | null
  configured: boolean
}

interface DashboardSummary {
  agent_stats: AgentStats
  cost_avoided_total: number | null
  time_saved_hours_total: number | null
  daily_run_counts: DailyRunCount[]
  recent_runs: RecentRun[]
  supabase_counts: SupabaseLiveCounts
}

// ============================================================================
// Animation variants
// ============================================================================

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.08 } },
}

const itemVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: [0.25, 0.46, 0.45, 0.94] as const } },
}

// ============================================================================
// Status display config — one place, shared by cards, chart, table, filter
// ============================================================================

const STATUS_META: Record<
  RunStatus,
  { label: string; badgeClass: string; dotClass: string; chartColor: string }
> = {
  running: { label: 'Running', badgeClass: 'bg-blue-50 text-blue-700 border-blue-200', dotClass: 'bg-blue-500', chartColor: '#3B82F6' },
  pending_approval: { label: 'Pending Approval', badgeClass: 'bg-amber-50 text-amber-700 border-amber-200', dotClass: 'bg-amber-500', chartColor: '#F59E0B' },
  auto_executed: { label: 'Auto-Executed', badgeClass: 'bg-emerald-50 text-emerald-700 border-emerald-200', dotClass: 'bg-emerald-500', chartColor: '#10B981' },
  approved: { label: 'Approved', badgeClass: 'bg-emerald-50 text-emerald-700 border-emerald-200', dotClass: 'bg-emerald-500', chartColor: '#059669' },
  rejected: { label: 'Rejected', badgeClass: 'bg-red-50 text-red-700 border-red-200', dotClass: 'bg-red-500', chartColor: '#EF4444' },
  failed: { label: 'Failed', badgeClass: 'bg-red-50 text-red-700 border-red-200', dotClass: 'bg-red-500', chartColor: '#DC2626' },
}

const DAY_RANGES = [7, 14, 30] as const

// ============================================================================
// Clickable metric card — doubles as a status filter for the table below
// ============================================================================

function MetricCard({
  title,
  value,
  icon: Icon,
  colorClass,
  delay = 0,
  isActive,
  onClick,
}: {
  title: string
  value: string
  icon: React.ElementType
  colorClass: string
  delay?: number
  isActive?: boolean
  onClick?: () => void
}) {
  return (
    <motion.div variants={itemVariants} transition={{ delay }} whileHover={{ y: -4 }}>
      <Card
        onClick={onClick}
        className={cn(
          'group relative h-full overflow-hidden',
          onClick && 'cursor-pointer',
          isActive && 'ring-2 ring-brand-cornflower'
        )}
      >
        <CardWatermark opacity={3} scale={0.9} />
        <CardContent className="relative z-10 p-5">
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <p className="text-micro uppercase text-brand-muted transition-colors duration-200 group-hover:text-brand-cornflower">
                {title}
              </p>
              <p className="font-display text-[2.25rem] font-bold leading-none tracking-tight text-brand-navy">
                {value}
              </p>
            </div>
            <div className={cn('rounded-xl p-2.5 text-white shadow-lg', colorClass)}>
              <Icon className="h-5 w-5" strokeWidth={1.5} />
            </div>
          </div>
          {onClick && (
            <p className="mt-2 text-xs text-brand-muted opacity-0 transition-opacity group-hover:opacity-100">
              Click to filter table ↓
            </p>
          )}
        </CardContent>
      </Card>
    </motion.div>
  )
}

// ============================================================================
// Live-from-Supabase strip
// ============================================================================

function SupabaseCountPill({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-center gap-2.5 rounded-xl border border-white/60 bg-white/70 px-4 py-2.5 shadow-sm">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
      <span className="text-sm text-brand-muted">{label}</span>
      <span className="font-display text-sm font-bold text-brand-navy">
        {value === null ? '—' : value.toLocaleString()}
      </span>
    </div>
  )
}

// ============================================================================
// Trend chart with a real day-range toggle
// ============================================================================

function DailyRunsChart({
  data,
  range,
  onRangeChange,
  isLoading,
}: {
  data: DailyRunCount[]
  range: number
  onRangeChange: (days: number) => void
  isLoading: boolean
}) {
  const chartData = data.map(d => ({
    name: new Date(d.date).toLocaleDateString(undefined, range > 7 ? { month: 'short', day: 'numeric' } : { weekday: 'short' }),
    runs: d.count,
  }))

  return (
    <Card className="relative col-span-12 h-full overflow-hidden lg:col-span-7">
      <CardWatermark opacity={3} scale={1.1} />
      <CardHeader className="relative z-10 flex flex-row items-center justify-between space-y-0">
        <CardTitle className="flex items-center gap-2">
          <Icons.activity className="h-5 w-5 text-brand-cornflower" strokeWidth={1.5} />
          Disruption Runs
        </CardTitle>
        <div className="flex gap-1 rounded-lg bg-muted/50 p-1">
          {DAY_RANGES.map(d => (
            <button
              key={d}
              onClick={() => onRangeChange(d)}
              className={cn(
                'rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
                range === d ? 'bg-white text-brand-navy shadow-sm' : 'text-brand-muted hover:text-brand-navy'
              )}
            >
              {d}D
            </button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="relative z-10">
        {isLoading ? (
          <Skeleton className="h-[220px] w-full rounded-xl" />
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={chartData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
              <defs>
                <linearGradient id="runsGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#535EA4" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="#535EA4" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
              <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#8890A6' }} axisLine={false} tickLine={false} interval={range > 14 ? 3 : 0} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: '#8890A6' }} axisLine={false} tickLine={false} width={30} />
              <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #E5E7EB', fontSize: 13 }} />
              <Area type="monotone" dataKey="runs" stroke="#535EA4" strokeWidth={2} fill="url(#runsGradient)" />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Status breakdown donut — new
// ============================================================================

function StatusBreakdownChart({ stats }: { stats: AgentStats | undefined }) {
  const data = stats
    ? (Object.keys(STATUS_META) as RunStatus[])
        .map(status => ({ name: STATUS_META[status].label, value: stats[status], color: STATUS_META[status].chartColor }))
        .filter(d => d.value > 0)
    : []

  return (
    <Card className="relative col-span-12 h-full overflow-hidden lg:col-span-5">
      <CardWatermark opacity={3} scale={1.1} />
      <CardHeader className="relative z-10">
        <CardTitle className="flex items-center gap-2">
          <Icons.barChart className="h-5 w-5 text-brand-cornflower" strokeWidth={1.5} />
          Status Breakdown
        </CardTitle>
      </CardHeader>
      <CardContent className="relative z-10">
        {data.length === 0 ? (
          <div className="flex h-[220px] items-center justify-center text-sm text-brand-muted">
            No runs yet
          </div>
        ) : (
          <div className="flex items-center gap-4">
            <ResponsiveContainer width="60%" height={200}>
              <PieChart>
                <Pie data={data} dataKey="value" nameKey="name" innerRadius={50} outerRadius={80} paddingAngle={2}>
                  {data.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ borderRadius: 12, border: '1px solid #E5E7EB', fontSize: 13 }} />
              </PieChart>
            </ResponsiveContainer>
            <div className="flex-1 space-y-2">
              {data.map(d => (
                <div key={d.name} className="flex items-center justify-between gap-2 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="h-2 w-2 rounded-full" style={{ backgroundColor: d.color }} />
                    <span className="text-brand-muted">{d.name}</span>
                  </div>
                  <span className="font-semibold text-brand-navy">{d.value}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Recent Activity table — now with search + status filter
// ============================================================================

function RecentActivityTable({
  runs,
  search,
  onSearchChange,
  statusFilter,
  onStatusFilterChange,
}: {
  runs: RecentRun[]
  search: string
  onSearchChange: (v: string) => void
  statusFilter: RunStatus | null
  onStatusFilterChange: (v: RunStatus | null) => void
}) {
  const filtered = useMemo(() => {
    return runs.filter(run => {
      if (statusFilter && run.status !== statusFilter) return false
      if (search.trim() && !run.supplier_label.toLowerCase().includes(search.trim().toLowerCase())) return false
      return true
    })
  }, [runs, statusFilter, search])

  return (
    <Card className="relative col-span-12 overflow-hidden">
      <CardWatermark opacity={3} scale={1.1} />
      <CardHeader className="relative z-10 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:space-y-0">
        <CardTitle className="flex items-center gap-2">
          <Icons.inbox className="h-5 w-5 text-brand-cornflower" strokeWidth={1.5} />
          Recent Activity
          {statusFilter && (
            <button
              onClick={() => onStatusFilterChange(null)}
              className="ml-1 inline-flex items-center gap-1 rounded-full bg-brand-cornflower/10 px-2 py-0.5 text-xs font-medium text-brand-cornflower hover:bg-brand-cornflower/20"
            >
              {STATUS_META[statusFilter].label}
              <Icons.close className="h-3 w-3" />
            </button>
          )}
        </CardTitle>
        <div className="relative w-full sm:w-64">
          <Icons.search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-brand-muted" />
          <Input
            placeholder="Search supplier..."
            className="h-9 pl-8 text-sm"
            value={search}
            onChange={e => onSearchChange(e.target.value)}
          />
        </div>
      </CardHeader>
      <CardContent className="relative z-10 p-0">
        {filtered.length === 0 ? (
          <p className="p-6 text-sm text-brand-muted">
            {runs.length === 0
              ? 'Nothing has come through the Orchestrator yet — trigger a disruption to see it here.'
              : 'No runs match your search/filter.'}
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs uppercase text-brand-muted">
                  <th className="px-5 py-3 font-medium">Supplier</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Cost Avoided</th>
                  <th className="px-5 py-3 font-medium">Time Saved</th>
                  <th className="px-5 py-3 font-medium">When</th>
                </tr>
              </thead>
              <tbody>
                <AnimatePresence>
                  {filtered.map(run => (
                    <motion.tr
                      key={run.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      className="border-b border-border/30 last:border-0"
                    >
                      <td className="px-5 py-3 font-medium text-brand-navy">{run.supplier_label}</td>
                      <td className="px-5 py-3">
                        <button
                          onClick={() => onStatusFilterChange(run.status)}
                          className={cn(
                            'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-transform hover:scale-105',
                            STATUS_META[run.status]?.badgeClass ?? 'bg-gray-50 text-gray-700 border-gray-200'
                          )}
                        >
                          {STATUS_META[run.status]?.label ?? run.status}
                        </button>
                      </td>
                      <td className="px-5 py-3 text-brand-muted">
                        {run.cost_avoided !== null ? `$${run.cost_avoided.toLocaleString()}` : '—'}
                      </td>
                      <td className="px-5 py-3 text-brand-muted">
                        {run.time_saved_hours !== null ? `${run.time_saved_hours.toFixed(1)}h` : '—'}
                      </td>
                      <td className="px-5 py-3 text-brand-muted">
                        {new Date(run.created_at).toLocaleString(undefined, {
                          month: 'short',
                          day: 'numeric',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </td>
                    </motion.tr>
                  ))}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ============================================================================
// Hero
// ============================================================================

function HeroSection() {
  return (
    <motion.div
      className="col-span-12 py-2"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.25, 0.46, 0.45, 0.94] }}
    >
      <h1 className="text-display-3 font-bold tracking-tight text-brand-navy lg:text-display-2">
        Ops Helper <span className="text-gradient">Command Center</span>
      </h1>
      <p className="mt-4 text-lg font-light text-muted-foreground">
        Live procurement exception activity, powered by your Orchestrator and Policy Engine.
      </p>
    </motion.div>
  )
}

// ============================================================================
// Main page
// ============================================================================

export default function HomePage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isChartLoading, setIsChartLoading] = useState(false)
  const [dayRange, setDayRange] = useState(7)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<RunStatus | null>(null)

  const load = useCallback(async (days: number, isRangeChange = false) => {
    if (isRangeChange) setIsChartLoading(true)
    try {
      const data = await apiClient.get<DashboardSummary>(`/api/dashboard/summary?days=${days}`)
      setSummary(data)
    } catch (err) {
      console.error('[Dashboard] Failed to load summary:', err)
    } finally {
      setIsLoading(false)
      setIsChartLoading(false)
    }
  }, [])

  useEffect(() => {
    load(dayRange)
    // Keep the dashboard genuinely live — refresh every 15s so numbers
    // move while the agent works.
    const interval = setInterval(() => load(dayRange), 15000)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleRangeChange = (days: number) => {
    setDayRange(days)
    load(days, true)
  }

  const toggleStatusFilter = (status: RunStatus) => {
    setStatusFilter(prev => (prev === status ? null : status))
  }

  const stats = summary?.agent_stats
  const supabase = summary?.supabase_counts

  return (
    <motion.div className="space-y-6" variants={containerVariants} initial="hidden" animate="visible">
      <HeroSection />

      {/* Live Supabase counts strip */}
      <motion.div variants={itemVariants} className="flex flex-wrap gap-3">
        {isLoading ? (
          <>
            <Skeleton className="h-11 w-40 rounded-xl" />
            <Skeleton className="h-11 w-48 rounded-xl" />
            <Skeleton className="h-11 w-44 rounded-xl" />
          </>
        ) : supabase?.configured ? (
          <>
            <SupabaseCountPill label="Suppliers" value={supabase.suppliers} />
            <SupabaseCountPill label="Open Disruption Notices" value={supabase.disruption_notices} />
            <SupabaseCountPill label="Inventory Positions" value={supabase.inventory_positions} />
            <SupabaseCountPill label="Active Shipments" value={supabase.shipments} />
          </>
        ) : (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-700">
            Supabase not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env to show live counts here.
          </div>
        )}
      </motion.div>

      {/* Clickable agent activity stat cards — click to filter the table below */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <MetricCard
          title="Pending Approval"
          value={isLoading ? '—' : String(stats?.pending_approval ?? 0)}
          icon={Icons.clock}
          colorClass="bg-amber-500"
          delay={0.1}
          isActive={statusFilter === 'pending_approval'}
          onClick={() => toggleStatusFilter('pending_approval')}
        />
        <MetricCard
          title="Auto-Executed"
          value={isLoading ? '—' : String(stats?.auto_executed ?? 0)}
          icon={Icons.checkCircle}
          colorClass="bg-emerald-600"
          delay={0.2}
          isActive={statusFilter === 'auto_executed'}
          onClick={() => toggleStatusFilter('auto_executed')}
        />
        <MetricCard
          title="Total Cost Avoided"
          value={
            isLoading
              ? '—'
              : summary?.cost_avoided_total !== null && summary?.cost_avoided_total !== undefined
                ? `$${summary.cost_avoided_total.toLocaleString()}`
                : '—'
          }
          icon={Icons.trendingUp}
          colorClass="bg-brand-navy"
          delay={0.3}
        />
        <MetricCard
          title="Total Time Saved"
          value={
            isLoading
              ? '—'
              : summary?.time_saved_hours_total !== null && summary?.time_saved_hours_total !== undefined
                ? `${summary.time_saved_hours_total.toFixed(1)}h`
                : '—'
          }
          icon={Icons.zap}
          colorClass="bg-brand-cornflower"
          delay={0.4}
        />
      </div>

      {/* Trend chart + status breakdown, side by side */}
      <motion.div variants={itemVariants} className="grid grid-cols-12 gap-4">
        <DailyRunsChart
          data={summary?.daily_run_counts ?? []}
          range={dayRange}
          onRangeChange={handleRangeChange}
          isLoading={isChartLoading}
        />
        {isLoading ? <Skeleton className="col-span-12 h-[300px] rounded-2xl lg:col-span-5" /> : <StatusBreakdownChart stats={stats} />}
      </motion.div>

      {/* Recent activity table — searchable + filterable */}
      <motion.div variants={itemVariants}>
        {isLoading ? (
          <Skeleton className="h-64 rounded-2xl" />
        ) : (
          <RecentActivityTable
            runs={summary?.recent_runs ?? []}
            search={search}
            onSearchChange={setSearch}
            statusFilter={statusFilter}
            onStatusFilterChange={setStatusFilter}
          />
        )}
      </motion.div>
    </motion.div>
  )
}
