import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer } from 'recharts'

interface FunnelStage {
  stage: string
  visitor_count: number
  drop_off_pct: number
}

const STAGE_LABELS: Record<string, string> = {
  ENTRY: 'Entry',
  ZONE_VISIT: 'Zone Visit',
  BILLING_QUEUE: 'Billing Queue',
  PURCHASE: 'Purchase',
}

const COLORS = ['#a855f7', '#7c3aed', '#6d28d9', '#22c55e']

export function FunnelChart({ stages }: { stages: FunnelStage[] }) {
  if (!stages.length) {
    return <div className="text-gray-600 text-sm text-center py-8">No data yet</div>
  }

  const data = stages.map((s, i) => ({
    name: STAGE_LABELS[s.stage] || s.stage,
    visitors: s.visitor_count,
    dropOff: s.drop_off_pct,
    color: COLORS[i] || '#6b7280',
  }))

  return (
    <div>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} layout="vertical" margin={{ left: 20, right: 20 }}>
          <XAxis type="number" tick={{ fill: '#9ca3af', fontSize: 11 }} />
          <YAxis type="category" dataKey="name" tick={{ fill: '#d1d5db', fontSize: 11 }} width={90} />
          <Tooltip
            contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8 }}
            labelStyle={{ color: '#f9fafb' }}
            formatter={(value, name) => [value, 'Visitors']}
          />
          <Bar dataKey="visitors" radius={[0, 4, 4, 0]}>
            {data.map((entry, index) => (
              <Cell key={index} fill={entry.color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      <div className="mt-3 flex flex-wrap gap-2">
        {stages.slice(1).map((s) => (
          <span key={s.stage} className="text-xs text-gray-500">
            {STAGE_LABELS[s.stage] || s.stage}: <span className="text-red-400">{s.drop_off_pct.toFixed(1)}% drop</span>
          </span>
        ))}
      </div>
    </div>
  )
}
