import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

// 交互式图表组件：直接渲染后端返回的 ECharts option（chart_json）
export default function ChartView({ option }) {
  const ref = useRef(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !option) return undefined;
    const chart = echarts.init(el);
    chart.setOption(option);
    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart.dispose();
    };
  }, [option]);

  if (!option) return null;
  return (
    <div className="mt-3">
      <div ref={ref} style={{ width: '100%', height: 360 }} />
      <div className="text-[11px] text-gray-400 mt-1">📊 交互图表（可悬停查看数据）</div>
    </div>
  );
}
