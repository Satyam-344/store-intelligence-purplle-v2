import { useEffect, useState } from 'react'
import { MetricCard } from './components/MetricCard'
import { FunnelChart } from './components/FunnelChart'
import { HeatmapGrid } from './components/HeatmapGrid'
import { AnomalyFeed } from './components/AnomalyFeed'
import { useWebSocket } from './hooks/useWebSocket'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'
const WS_BASE = import.meta.env.VITE_WS_BASE || 'ws://localhost:8000'
const STORE_ID = 'STORE_BLR_002'
const REFRESH_MS = 5000

interface Metrics {
  unique_visitors: number
  conversion_rate: number
  current_queue_depth: number
  abandonment_rate: number
  total_transactions: number
  avg_basket_value_inr: number | null
}

interface FunnelStage {
  stage: string
  visitor_count: number
  drop_off_pct: number
}

interface HeatmapZone {
  zone_id: string
  zone_name: string
  visit_frequency: number
  avg_dwell_ms: number
  normalised_score: number
}

interface Anomaly {
  anomaly_id: string
  anomaly_type: string
  severity: string
  description: string
  suggested_action: string
  detected_at: string
}

export default function App() {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [funnel, setFunnel] = useState<FunnelStage[]>([])
  const [heatmap, setHeatmap] = useState<HeatmapZone[]>([])
  const [anomalies, setAnomalies] = useState<Anomaly[]>([])
  const [lastUpdate, setLastUpdate] = useState<string>('')
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting')

  const { lastMessage } = useWebSocket(`${WS_BASE}/ws/${STORE_ID}`, {
    onOpen: () => setConnectionStatus('connected'),
    onClose: () => setConnectionStatus('disconnected'),
  })

  const fetchAll = async () => {
    try {
      const [metricsRes, funnelRes, heatmapRes, anomaliesRes] = await Promise.all([
        fetch(`${API_BASE}/stores/${STORE_ID}/metrics`),
        fetch(`${API_BASE}/stores/${STORE_ID}/funnel`),
        fetch(`${API_BASE}/stores/${STORE_ID}/heatmap`),
        fetch(`${API_BASE}/stores/${STORE_ID}/anomalies`),
      ])
      if (metricsRes.ok) setMetrics(await metricsRes.json())
      if (funnelRes.ok) {
        const data = await funnelRes.json()
        setFunnel(data.stages || [])
      }
      if (heatmapRes.ok) {
        const data = await heatmapRes.json()
        setHeatmap(data.zones || [])
      }
      if (anomaliesRes.ok) {
        const data = await anomaliesRes.json()
        setAnomalies(data.active_anomalies || [])
      }
      setLastUpdate(new Date().toLocaleTimeString())
    } catch (e) {
      console.error('Failed to fetch data', e)
    }
  }

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, REFRESH_MS)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    if (lastMessage) {
      fetchAll()
    }
  }, [lastMessage])

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <header className="mb-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-purple-400">Store Intelligence</h1>
            <p className="text-gray-400 text-sm mt-1">{STORE_ID} · Live Analytics Dashboard</p>
          </div>
          <div className="flex items-center gap-3">
            <div className={`w-2 h-2 rounded-full ${
              connectionStatus === 'connected' ? 'bg-green-400 animate-pulse' :
              connectionStatus === 'disconnected' ? 'bg-red-400' : 'bg-yellow-400'
            }`} />
            <span className="text-xs text-gray-400">
              {connectionStatus === 'connected' ? 'Live' : connectionStatus}
            </span>
            {lastUpdate && (
              <span className="text-xs text-gray-500">Updated {lastUpdate}</span>
            )}
          </div>
        </div>
      </header>

      {anomalies.length > 0 && (
        <div className="mb-6">
          <AnomalyFeed anomalies={anomalies} />
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4 mb-8">
        <MetricCard
          label="Visitors Today"
          value={metrics?.unique_visitors ?? '—'}
          accent="purple"
        />
        <MetricCard
          label="Conversion"
          value={metrics ? `${(metrics.conversion_rate * 100).toFixed(1)}%` : '—'}
          accent="green"
        />
        <MetricCard
          label="Queue Depth"
          value={metrics?.current_queue_depth ?? '—'}
          accent={metrics && metrics.current_queue_depth > 5 ? 'red' : 'yellow'}
        />
        <MetricCard
          label="Abandonment"
          value={metrics ? `${(metrics.abandonment_rate * 100).toFixed(1)}%` : '—'}
          accent="orange"
        />
        <MetricCard
          label="Transactions"
          value={metrics?.total_transactions ?? '—'}
          accent="blue"
        />
        <MetricCard
          label="Avg Basket"
          value={metrics?.avg_basket_value_inr != null ? `₹${metrics.avg_basket_value_inr}` : '—'}
          accent="teal"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
          <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wide">
            Conversion Funnel
          </h2>
          <FunnelChart stages={funnel} />
        </div>

        <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
          <h2 className="text-sm font-semibold text-gray-300 mb-4 uppercase tracking-wide">
            Zone Heatmap
          </h2>
          <HeatmapGrid zones={heatmap} />
        </div>
      </div>
    </div>
  )
}
