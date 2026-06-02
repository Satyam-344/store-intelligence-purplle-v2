interface MetricCardProps {
  label: string
  value: string | number
  accent?: 'purple' | 'green' | 'yellow' | 'red' | 'blue' | 'orange' | 'teal'
}

const accentClasses: Record<string, string> = {
  purple: 'text-purple-400',
  green: 'text-green-400',
  yellow: 'text-yellow-400',
  red: 'text-red-400',
  blue: 'text-blue-400',
  orange: 'text-orange-400',
  teal: 'text-teal-400',
}

export function MetricCard({ label, value, accent = 'purple' }: MetricCardProps) {
  return (
    <div className="bg-gray-900 rounded-xl p-4 border border-gray-800 flex flex-col gap-1">
      <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-2xl font-bold ${accentClasses[accent]}`}>{value}</span>
    </div>
  )
}
