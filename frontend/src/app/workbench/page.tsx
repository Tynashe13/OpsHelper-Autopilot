'use client'

import { useCallback, useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { CardWatermark } from '@/components/ui/card-watermark'
import { Icons } from '@/components/ui/icons'
import { toast } from '@/components/ui/toast'

// ============================================================================
// Types — mirror app/schemas/workbench.py's WorkbenchItemResponse /
// WorkbenchSummary exactly, same convention as PolicyCard.tsx's Policy type
// ============================================================================

interface WorkbenchItem {
  id: string
  title: string
  description: string
  entity_name: string | null
  entity_id: string | null
  source: string
  payload: Record<string, unknown> | null
  reason: string | null
  status: 'pending' | 'in_progress' | 'resolved' | 'escalated' | 'cancelled'
  priority: 'low' | 'medium' | 'high' | 'critical'
  notify_target: string | null
  assigned_to: string | null
  retry_count: number
  max_retries: number
  retry_interval_minutes: number
  last_notified_at: string | null
  next_retry_at: string | null
  escalated: boolean
  escalated_to: string | null
  escalated_at: string | null
  resolution: string | null
  resolved_by: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
}

interface WorkbenchSummary {
  total: number
  pending: number
  in_progress: number
  resolved: number
  escalated: number
  cancelled: number
  by_priority: Record<string, number>
}

// ============================================================================
// Animation Variants
// ============================================================================

const containerVariants = {
  hidden: { opacity: 0 },
  visible: { opacity: 1, transition: { staggerChildren: 0.05 } },
}
const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

// ============================================================================
// Presentation helpers
// ============================================================================

const PRIORITY_STYLES: Record<string, string> = {
  low: 'bg-gray-100 text-gray-600',
  medium: 'bg-blue-100 text-blue-700',
  high: 'bg-amber-100 text-amber-700',
  critical: 'bg-red-100 text-red-700',
}

const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-700',
  in_progress: 'bg-blue-100 text-blue-700',
  resolved: 'bg-emerald-100 text-emerald-700',
  escalated: 'bg-red-100 text-red-700',
  cancelled: 'bg-gray-100 text-gray-500',
}

function Badge({ label, className }: { label: string; className: string }) {
  return (
    <span className={cn('rounded-full px-2.5 py-0.5 text-xs font-medium capitalize', className)}>
      {label.replace('_', ' ')}
    </span>
  )
}

// Sample disruption records — the Simulate Disruption button picks one at
// random each click. Deliberately spans the full range: some clear the
// $5,000 seed-policy threshold (routes to Workbench), some don't
// (auto-resolves) — so repeated clicks demonstrate both paths of the
// Orchestrator -> Triage decision, not just one.
const SAMPLE_DISRUPTIONS = [
  { title: 'Late shipment — PO-4471 (electronics)', estimated_cost: 12500 },
  { title: 'Carrier delay — container SEA-8823', estimated_cost: 3200 },
  { title: 'Customs hold — PO-9012 (perishables)', estimated_cost: 27800 },
  { title: 'Minor routing reroute — local courier', estimated_cost: 450 },
  { title: 'Warehouse damage claim — SKU-2291', estimated_cost: 8900 },
  { title: 'Vendor short-ship — PO-5567', estimated_cost: 1800 },
]

export default function WorkbenchPage() {
  const [items, setItems] = useState<WorkbenchItem[]>([])
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [isSimulating, setIsSimulating] = useState(false)
  const [resolvingId, setResolvingId] = useState<string | null>(null)
  const [resolutionText, setResolutionText] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)

  const loadWorkbench = useCallback(async () => {
    setIsLoading(true)
    setLoadError(null)
    try {
      const [itemsData, summaryData] = await Promise.all([
        apiClient.get<WorkbenchItem[]>('/api/workbench'),
        apiClient.get<WorkbenchSummary>('/api/workbench/summary'),
      ])
      setItems(itemsData)
      setSummary(summaryData)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load Workbench.')
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadWorkbench()
  }, [loadWorkbench])

  // The live trigger: hands a sample disruption notice to the real
  // Orchestrator ingest endpoint (POST /api/orchestrator/events), which
  // runs the Policy Engine and then decides auto_resolve vs.
  // route_to_workbench — same pipeline a real Auto/Slack event would go
  // through, just with a manually-picked record instead of a live feed.
  const handleSimulate = useCallback(async () => {
    setIsSimulating(true)
    try {
      const sample = SAMPLE_DISRUPTIONS[Math.floor(Math.random() * SAMPLE_DISRUPTIONS.length)]
      const result = await apiClient.post<{
        decision: string
        priority: string
        reasoning: string
        matched_count: number
        workbench_item_id: string | null
      }>('/api/orchestrator/events', {
        entity_name: 'disruption_notice',
        record: {
          id: `SIM-${Date.now()}`,
          title: sample.title,
          estimated_cost: sample.estimated_cost,
        },
        source: 'manual_simulation',
        notify_target: 'commander',
      })

      if (result.decision === 'route_to_workbench') {
        toast.success('Disruption routed to Workbench', {
          description: `${sample.title} — priority: ${result.priority}`,
        })
      } else {
        toast.success('Disruption auto-resolved', {
          description: `${sample.title} — no human review needed`,
        })
      }
      await loadWorkbench()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Simulation failed.')
    } finally {
      setIsSimulating(false)
    }
  }, [loadWorkbench])

  const handleResolve = useCallback(async (id: string) => {
    if (!resolutionText.trim()) return
    setBusyId(id)
    try {
      await apiClient.post(`/api/workbench/${id}/resolve`, { resolution: resolutionText.trim() })
      toast.success('Item resolved')
      setResolvingId(null)
      setResolutionText('')
      await loadWorkbench()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to resolve item.')
    } finally {
      setBusyId(null)
    }
  }, [resolutionText, loadWorkbench])

  const handleEscalate = useCallback(async (id: string) => {
    setBusyId(id)
    try {
      await apiClient.post(`/api/workbench/${id}/escalate`, { escalated_to: 'commander-escalations' })
      toast.success('Item escalated')
      await loadWorkbench()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to escalate item.')
    } finally {
      setBusyId(null)
    }
  }, [loadWorkbench])

  const filteredItems = statusFilter === 'all' ? items : items.filter((i) => i.status === statusFilter)

  return (
    <motion.div className="space-y-6" variants={containerVariants} initial="hidden" animate="visible">
      {/* Header */}
      <motion.div variants={itemVariants} className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-display-3 font-bold tracking-tight text-brand-navy lg:text-display-2">
            Workbench
          </h1>
          <p className="mt-1 text-lg text-muted-foreground">
            Exceptions the system routed to a human — resolve, escalate, or track retries.
          </p>
        </div>
        <Button variant="gradient" onClick={handleSimulate} disabled={isSimulating}>
          {isSimulating ? (
            <Icons.loader className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Icons.zap className="mr-2 h-4 w-4" />
          )}
          Simulate Disruption
        </Button>
      </motion.div>

      {/* Load error banner */}
      {loadError && (
        <motion.div
          variants={itemVariants}
          className="flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
        >
          <div className="flex items-center gap-2">
            <Icons.alertCircle className="h-4 w-4 flex-shrink-0" />
            <span>{loadError}</span>
          </div>
          <Button variant="ghost" size="sm" onClick={() => loadWorkbench()}>
            <Icons.loader className="mr-1.5 h-3.5 w-3.5" />
            Retry
          </Button>
        </motion.div>
      )}

      {/* Summary Bar */}
      {summary && (
        <motion.div variants={itemVariants} className="grid grid-cols-2 sm:grid-cols-5 gap-4">
          {[
            { key: 'all', value: summary.total, label: 'Total', icon: Icons.inbox, color: 'text-brand-navy' },
            { key: 'pending', value: summary.pending, label: 'Pending', icon: Icons.clock, color: 'text-amber-600' },
            { key: 'escalated', value: summary.escalated, label: 'Escalated', icon: Icons.alertTriangle, color: 'text-red-600' },
            { key: 'resolved', value: summary.resolved, label: 'Resolved', icon: Icons.checkCircle, color: 'text-emerald-600' },
            { key: 'in_progress', value: summary.in_progress, label: 'In Progress', icon: Icons.activity, color: 'text-blue-600' },
          ].map((stat) => (
            <motion.button
              key={stat.key}
              onClick={() => setStatusFilter(stat.key)}
              className={cn(
                'bg-white rounded-xl border p-4 text-left transition-all',
                statusFilter === stat.key ? 'border-brand-cornflower shadow-md' : 'border-gray-200 hover:border-gray-300'
              )}
              whileHover={{ y: -2 }}
            >
              <div className="flex items-center gap-3">
                <stat.icon className={cn('h-5 w-5', stat.color)} />
                <div>
                  <p className={cn('text-2xl font-bold', stat.color)}>{stat.value}</p>
                  <p className="text-xs text-muted-foreground">{stat.label}</p>
                </div>
              </div>
            </motion.button>
          ))}
        </motion.div>
      )}

      {/* Item List */}
      <motion.div variants={itemVariants}>
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Icons.loader className="h-8 w-8 animate-spin text-brand-cornflower" />
          </div>
        ) : filteredItems.length === 0 ? (
          <Card className="relative overflow-hidden">
            <CardWatermark opacity={3} scale={1} />
            <CardContent className="relative z-10 flex flex-col items-center justify-center py-16 text-center">
              <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-cornflower/20 to-brand-purple/20">
                <Icons.inbox className="h-8 w-8 text-brand-cornflower" strokeWidth={1.5} />
              </div>
              <h3 className="font-display text-lg font-semibold text-brand-navy">
                {statusFilter === 'all' ? 'Nothing in the queue' : `No ${statusFilter.replace('_', ' ')} items`}
              </h3>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                Click &ldquo;Simulate Disruption&rdquo; to send a sample event through the real Policy
                Engine → Orchestrator → Workbench pipeline.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            <AnimatePresence>
              {filteredItems.map((item) => {
                const isTerminal = item.status === 'resolved' || item.status === 'cancelled'
                const isResolving = resolvingId === item.id
                const isBusy = busyId === item.id
                return (
                  <motion.div
                    key={item.id}
                    layout
                    initial={{ opacity: 0, y: 10 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -10 }}
                  >
                    <Card className="relative overflow-hidden">
                      <CardContent className="relative z-10 p-5">
                        <div className="flex flex-col gap-3">
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                              <h3 className="font-semibold text-brand-navy truncate">{item.title}</h3>
                              {item.reason && (
                                <p className="mt-0.5 text-sm text-muted-foreground">{item.reason}</p>
                              )}
                            </div>
                            <div className="flex flex-shrink-0 items-center gap-2">
                              <Badge label={item.priority} className={PRIORITY_STYLES[item.priority] || PRIORITY_STYLES.medium} />
                              <Badge label={item.status} className={STATUS_STYLES[item.status] || STATUS_STYLES.pending} />
                            </div>
                          </div>

                          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                            {item.entity_name && <span>Entity: {item.entity_name}</span>}
                            <span>Source: {item.source}</span>
                            <span>Retries: {item.retry_count}/{item.max_retries}</span>
                            {item.escalated && item.escalated_to && (
                              <span className="text-red-600">Escalated to: {item.escalated_to}</span>
                            )}
                            {item.resolved_by && (
                              <span>Resolved by: {item.resolved_by}</span>
                            )}
                          </div>

                          {!isTerminal && (
                            <div className="flex flex-wrap items-center gap-2 pt-1">
                              {!isResolving ? (
                                <>
                                  <Button
                                    size="sm"
                                    variant="default"
                                    disabled={isBusy}
                                    onClick={() => { setResolvingId(item.id); setResolutionText('') }}
                                  >
                                    <Icons.check className="mr-1.5 h-3.5 w-3.5" />
                                    Resolve
                                  </Button>
                                  {item.status !== 'escalated' && (
                                    <Button
                                      size="sm"
                                      variant="outline"
                                      disabled={isBusy}
                                      onClick={() => handleEscalate(item.id)}
                                    >
                                      {isBusy ? (
                                        <Icons.loader className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                      ) : (
                                        <Icons.alertTriangle className="mr-1.5 h-3.5 w-3.5" />
                                      )}
                                      Escalate
                                    </Button>
                                  )}
                                </>
                              ) : (
                                <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center">
                                  <input
                                    autoFocus
                                    type="text"
                                    value={resolutionText}
                                    onChange={(e) => setResolutionText(e.target.value)}
                                    placeholder="What was decided? (required)"
                                    className="flex-1 rounded-lg border border-input bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-cornflower/50"
                                  />
                                  <div className="flex gap-2">
                                    <Button
                                      size="sm"
                                      disabled={!resolutionText.trim() || isBusy}
                                      onClick={() => handleResolve(item.id)}
                                    >
                                      {isBusy ? (
                                        <Icons.loader className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                                      ) : (
                                        <Icons.check className="mr-1.5 h-3.5 w-3.5" />
                                      )}
                                      Confirm
                                    </Button>
                                    <Button size="sm" variant="ghost" onClick={() => setResolvingId(null)}>
                                      Cancel
                                    </Button>
                                  </div>
                                </div>
                              )}
                            </div>
                          )}

                          {isTerminal && item.resolution && (
                            <div className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                              <span className="font-medium">Resolution:</span> {item.resolution}
                            </div>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  </motion.div>
                )
              })}
            </AnimatePresence>
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}
