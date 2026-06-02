interface Anomaly {
  anomaly_id: string
  anomaly_type: string
  severity: string
  description: string
  suggested_action: string
  detected_at: string
}

const severityStyles: Record<string, string> = {
  CRITICAL: 'border-red-500 bg-red-950 text-red-300',
  WARN: 'border-yellow-500 bg-yellow-950 text-yellow-300',
  INFO: 'border-blue-500 bg-blue-950 text-blue-300',
}

const severityBadge: Record<string, string> = {
  CRITICAL: 'bg-red-600 text-white',
  WARN: 'bg-yellow-600 text-white',
  INFO: 'bg-blue-600 text-white',
}

export function AnomalyFeed({ anomalies }: { anomalies: Anomaly[] }) {
  return (
    <div className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Active Anomalies</h2>
      {anomalies.map((a) => (
        <div
          key={a.anomaly_id}
          className={`rounded-xl px-4 py-3 border ${severityStyles[a.severity] || 'border-gray-700 bg-gray-900'}`}
        >
          <div className="flex items-start gap-3">
            <span className={`text-xs font-bold px-2 py-0.5 rounded ${severityBadge[a.severity]}`}>
              {a.severity}
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium">{a.anomaly_type.replace(/_/g, ' ')}</div>
              <div className="text-xs mt-0.5 opacity-80">{a.description}</div>
              <div className="text-xs mt-1 font-medium opacity-60">→ {a.suggested_action}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
