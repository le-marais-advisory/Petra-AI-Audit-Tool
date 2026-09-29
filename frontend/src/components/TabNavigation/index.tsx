import { cn } from "@/utils/cn";

import { tabDefinitions, type TabNavigationProps } from "./behaviors";


export function TabNavigation({ activeTab, onTabChange, tabs = tabDefinitions }: TabNavigationProps) {
  return (
    <div className="flex flex-wrap gap-2 border-b border-slate-200 px-5 py-4">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          className={cn(
            "rounded-full px-4 py-2 text-sm font-semibold transition",
            activeTab === tab.key ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200",
          )}
          type="button"
          onClick={() => onTabChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
