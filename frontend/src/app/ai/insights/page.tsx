'use client'

import { useState, useEffect, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { motion, AnimatePresence } from 'framer-motion'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { CardWatermark } from '@/components/ui/card-watermark'
import { Icons } from '@/components/ui/icons'
import { InsightCard, type Insight } from '@/components/ai/insights/InsightCard'
import { PatternCluster, type Pattern } from '@/components/ai/insights/PatternCluster'
import { ActionCard, type ActionItem } from '@/components/ai/insights/ActionCard'

// Matches app/schemas/insights.py's InsightsResponse exactly — this page
// is wired to the real backend (GET/POST /api/ai/insights), which
// aggregates real PolicyEvaluation/WorkbenchItem rows (see
// app/services/insights.py). No demo/hardcoded data below.
interface InsightsResponse {
  insights: Insight[]
  patterns: Pattern[]
  actions: ActionItem[]
  generated_at: string
  based_on_records: number
}

// Tab configuration
interface Tab {
  id: string
  label: string
  icon: React.ElementType
}

const tabs: Tab[] = [
  { id: 'summary', label: 'Summary', icon: Icons.activity },
  { id: 'patterns', label: 'Patterns', icon: Icons.layers },
  { id: 'actions', label: 'Actions', icon: Icons.zap },
]

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.1 },
  },
}

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

export default function AIInsightsPage() {
  const [activeTab, setActiveTab] = useState('summary')
  const [insights, setInsights] = useState<Insight[]>([])
  const [patterns, setPatterns] = useState<Pattern[]>([])
  const [actions, setActions] = useState<ActionItem[]>([])
  const [basedOnRecords, setBasedOnRecords] = useState(0)
  const [generatedAt, setGeneratedAt] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const router = useRouter()

  const applyResponse = useCallback((data: InsightsResponse) => {
    setInsights(data.insights)
    setPatterns(data.patterns)
    setActions(data.actions)
    setBasedOnRecords(data.based_on_records)
    setGeneratedAt(data.generated_at)
  }, [])

  const fetchInsights = useCallback(async () => {
    setIsLoading(true)
    setLoadError(null)
    try {
      const data = await apiClient.get<InsightsResponse>('/api/ai/insights')
      applyResponse(data)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to load insights.')
    } finally {
      setIsLoading(false)
    }
  }, [applyResponse])

  useEffect(() => {
    fetchInsights()
  }, [fetchInsights])

  const handleAnalyze = async () => {
    setIsAnalyzing(true)
    setLoadError(null)
    try {
      const data = await apiClient.post<InsightsResponse>('/api/ai/insights/refresh')
      applyResponse(data)
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : 'Failed to generate insights.')
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleInsightAction = useCallback((insight: Insight) => {
    // Route based on action_type
    switch (insight.action_type) {
      case 'create_policy':
        router.push('/ai/policies?tab=create-with-ai')
        break
      case 'investigate':
      case 'review_duplicate':
        router.push('/workbench')
        break
      default:
        break
    }
  }, [router])

  const handleDismissInsight = useCallback((id: string) => {
    // Optimistic UI update — insights are recomputed from live data on the
    // next refresh, so this only affects what's shown until then.
    setInsights(prev => prev.filter(i => i.id !== id))
  }, [])

  const handleApplyAction = useCallback((action: ActionItem) => {
    // Route based on action type
    switch (action.action_type) {
      case 'create_policy':
        router.push('/ai/policies?tab=create-with-ai')
        break
      case 'investigate':
      case 'review_transaction':
        router.push('/workbench')
        break
      default:
        break
    }
  }, [router])

  // Stats for summary
  const criticalCount = insights.filter(i => i.severity === 'critical').length
  const warningCount = insights.filter(i => i.severity === 'warning').length
  const infoCount = insights.filter(i => i.severity === 'info').length

  return (
    <motion.div
      className="space-y-6"
      variants={containerVariants}
      initial="hidden"
      animate="visible"
    >
      {/* Header */}
      <motion.div variants={itemVariants} className="flex items-center justify-between">
        <div>
          <h1 className="text-display-3 font-bold tracking-tight text-brand-navy lg:text-display-2">
            AI Insights
          </h1>
          <p className="mt-2 text-lg text-muted-foreground">
            Real analysis of your Policy Engine and Workbench data. Discover patterns,
            anomalies, and optimization opportunities.
          </p>
        </div>
        <Button
          variant="gradient"
          onClick={handleAnalyze}
          disabled={isAnalyzing}
        >
          {isAnalyzing ? (
            <>
              <Icons.loader className="mr-2 h-4 w-4 animate-spin" />
              Analyzing...
            </>
          ) : (
            <>
              <Icons.sparkles className="mr-2 h-4 w-4" strokeWidth={1.5} />
              Run Analysis
            </>
          )}
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
          <Button variant="ghost" size="sm" onClick={() => fetchInsights()}>
            <Icons.loader className="mr-1.5 h-3.5 w-3.5" />
            Retry
          </Button>
        </motion.div>
      )}

      {/* Data provenance notice */}
      {!loadError && (
        <motion.div
          variants={itemVariants}
          className="rounded-lg border border-border/50 bg-white/50 p-4"
        >
          <div className="flex items-start gap-3">
            <Icons.info className="h-5 w-5 text-brand-cornflower mt-0.5" />
            <div className="flex-1">
              <p className="text-sm text-muted-foreground">
                Based on <span className="font-medium text-foreground">{basedOnRecords}</span>{' '}
                real policy evaluation / Workbench record(s)
                {generatedAt && (
                  <> · generated {new Date(generatedAt).toLocaleString()}</>
                )}
                .
              </p>
            </div>
          </div>
        </motion.div>
      )}

      {/* Stats Cards */}
      <motion.div variants={itemVariants} className="grid gap-4 sm:grid-cols-3">
        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-red-100">
              <Icons.alertCircle className="h-6 w-6 text-red-600" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{criticalCount}</p>
              <p className="text-sm text-muted-foreground">Critical Issues</p>
            </div>
          </CardContent>
        </Card>

        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-amber-100">
              <Icons.alertTriangle className="h-6 w-6 text-amber-600" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{warningCount}</p>
              <p className="text-sm text-muted-foreground">Warnings</p>
            </div>
          </CardContent>
        </Card>

        <Card className="relative overflow-hidden">
          <CardWatermark opacity={2} scale={0.8} />
          <CardContent className="relative z-10 flex items-center gap-4 py-6">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-blue-100">
              <Icons.lightbulb className="h-6 w-6 text-blue-600" strokeWidth={1.5} />
            </div>
            <div>
              <p className="text-2xl font-bold text-brand-navy">{infoCount + patterns.length}</p>
              <p className="text-sm text-muted-foreground">Recommendations</p>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Tab Navigation */}
      <motion.div variants={itemVariants}>
        <div className={cn(
          'inline-flex items-center gap-1 rounded-xl p-1',
          'bg-white/50 border border-border/50',
          'backdrop-blur-sm'
        )}>
          {tabs.map((tab) => {
            const isActive = activeTab === tab.id
            const Icon = tab.icon

            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'relative flex items-center gap-2 rounded-lg px-4 py-2.5',
                  'text-sm font-medium transition-all duration-200',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-cornflower/50',
                  isActive
                    ? 'text-white'
                    : 'text-muted-foreground hover:text-foreground hover:bg-white/50'
                )}
              >
                {isActive && (
                  <motion.div
                    layoutId="activeInsightTab"
                    className="absolute inset-0 rounded-lg bg-brand-navy shadow-soft"
                    transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                  />
                )}
                <span className="relative z-10 flex items-center gap-2">
                  <Icon className="h-4 w-4" strokeWidth={1.5} />
                  <span className="hidden sm:inline">{tab.label}</span>
                </span>
              </button>
            )
          })}
        </div>
      </motion.div>

      {/* Tab Content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={activeTab}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.2 }}
        >
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Icons.loader className="h-8 w-8 animate-spin text-brand-cornflower" />
            </div>
          ) : (
            <>
              {activeTab === 'summary' && (
                <Card className="relative overflow-hidden">
                  <CardWatermark opacity={2} scale={1} />
                  <CardHeader className="relative z-10">
                    <CardTitle>All Insights</CardTitle>
                    <CardDescription>
                      {insights.length} insight(s) generated from real Policy Engine and
                      Workbench data.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="relative z-10 space-y-4">
                    {insights.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-12 text-center">
                        <div className={cn(
                          'mb-4 flex h-16 w-16 items-center justify-center rounded-2xl',
                          'bg-gradient-to-br from-brand-cornflower/20 to-brand-purple/20'
                        )}>
                          <Icons.lightbulb className="h-8 w-8 text-brand-cornflower" strokeWidth={1.5} />
                        </div>
                        <h3 className="font-display text-lg font-semibold text-brand-navy">
                          No insights yet
                        </h3>
                        <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                          Insights are generated from real Policy Evaluation and Workbench
                          activity — trigger a few events through the Orchestrator, then run
                          an analysis.
                        </p>
                        <Button
                          variant="gradient"
                          className="mt-6"
                          onClick={handleAnalyze}
                          disabled={isAnalyzing}
                        >
                          <Icons.sparkles className="mr-2 h-4 w-4" strokeWidth={1.5} />
                          Generate Insights
                        </Button>
                      </div>
                    ) : (
                      insights.map((insight) => (
                        <InsightCard
                          key={insight.id}
                          insight={insight}
                          onAction={handleInsightAction}
                          onDismiss={handleDismissInsight}
                        />
                      ))
                    )}
                  </CardContent>
                </Card>
              )}

              {activeTab === 'patterns' && (
                <Card className="relative overflow-hidden">
                  <CardWatermark opacity={2} scale={1} />
                  <CardHeader className="relative z-10">
                    <CardTitle>Detected Patterns</CardTitle>
                    <CardDescription>
                      Recurring policy matches identified from real evaluation data.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="relative z-10">
                    <PatternCluster patterns={patterns} />
                  </CardContent>
                </Card>
              )}

              {activeTab === 'actions' && (
                <Card className="relative overflow-hidden">
                  <CardWatermark opacity={2} scale={1} />
                  <CardHeader className="relative z-10">
                    <CardTitle>Recommended Actions</CardTitle>
                    <CardDescription>
                      Suggested improvements based on your real insights.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="relative z-10 space-y-3">
                    {actions.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-12 text-center">
                        <div className={cn(
                          'mb-4 flex h-12 w-12 items-center justify-center rounded-xl',
                          'bg-muted/50'
                        )}>
                          <Icons.zap className="h-6 w-6 text-muted-foreground" strokeWidth={1.5} />
                        </div>
                        <p className="text-sm text-muted-foreground">
                          No actions recommended at this time.
                        </p>
                      </div>
                    ) : (
                      actions.map((action, idx) => (
                        <ActionCard
                          key={idx}
                          action={action}
                          onApply={handleApplyAction}
                        />
                      ))
                    )}
                  </CardContent>
                </Card>
              )}
            </>
          )}
        </motion.div>
      </AnimatePresence>
    </motion.div>
  )
}
