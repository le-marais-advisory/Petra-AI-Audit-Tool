import { EmptyState } from "@/components/EmptyState";
import { ExtractionPageCard } from "@/components/ExtractionPageCard";

import type { ExtractionResultsProps } from "./behaviors";


export function ExtractionResults({ pages }: ExtractionResultsProps) {
  if (!pages.length) {
    return <EmptyState title="No extraction yet" description="Upload a document to inspect its extracted content." />;
  }

  return (
    <div className="space-y-6">
      {pages.map((page) => (
        <ExtractionPageCard key={page.page} page={page} />
      ))}
    </div>
  );
}
