interface HeatmapZone {
  zone_id: string
  zone_name: string
  visit_frequency: number
  avg_dwell_ms: number
  normalised_score: number
}

function scoreToColor(score: number): string {
  if (score >= 80) return 'bg-purple-600'
  if (score >= 60) return 'bg-purple-500'
  if (score >= 40) return 'bg-purple-400'
  if (score >= 20) return 'bg-purple-300'
  return 'bg-gray-700'
}

function formatDwell(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(0)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

export function HeatmapGrid({ zones }: { zones: HeatmapZone[] }) {
  if (!zones.length) {
    return <div className="text-gray-600 text-sm text-center py-8">No zone data yet</div>
  }

  return (
    <div className="grid grid-cols-2 gap-3">
      {zones.map((zone) => (
        <div
          key={zone.zone_id}
          className={`rounded-lg p-3 ${scoreToColor(zone.normalised_score)} bg-opacity-30 border border-gray-700`}
        >
          <div className="flex justify-between items-start mb-1">
            <span className="text-xs font-medium text-white truncate">{zone.zone_name}</span>
            <span className="text-xs text-gray-300 ml-1 shrink-0">{zone.normalised_score.toFixed(0)}</span>
          </div>
          <div className="text-xs text-gray-400">
            {zone.visit_frequency} visits · {formatDwell(zone.avg_dwell_ms)} avg
          </div>
          <div className="mt-2 h-1 rounded-full bg-gray-800">
            <div
              className="h-1 rounded-full bg-purple-500"
              style={{ width: `${zone.normalised_score}%` }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}
