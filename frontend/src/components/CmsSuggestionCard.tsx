import type { DragEvent } from 'react';
import type { GraphTemplate } from '../types/contracts';
import { nodeAccent } from '../utils/nodeAccent';

/** Drag payload for a CMS layer suggestion. Click and drop both call `onPlace`. */
export const CMS_LAYER_DRAG_TYPE = 'application/x-roaringlion-cms-layer';

export function CmsSuggestionCard({
  template,
  onCanvas,
  onPlace,
}: {
  template: GraphTemplate;
  onCanvas: boolean;
  onPlace: (template: GraphTemplate) => void;
}) {
  const pricing = template.pricingOutput;
  const onDragStart = (event: DragEvent<HTMLDivElement>) => {
    event.dataTransfer.setData(CMS_LAYER_DRAG_TYPE, template.templateId);
    event.dataTransfer.setData('text/plain', template.templateId);
    event.dataTransfer.effectAllowed = 'copy';
  };

  return (
    <div
      className={`palette-item suggested${pricing ? ' has-tree' : ''}`}
      role="button"
      tabIndex={0}
      draggable={Boolean(pricing)}
      aria-label={
        pricing
          ? `Place ${template.label} with its pricing output`
          : `Place ${template.label}`
      }
      onClick={() => onPlace(template)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onPlace(template);
        }
      }}
      onDragStart={onDragStart}
    >
      <span className="palette-head">
        <span className="palette-cat">{template.category}</span>
        {onCanvas && <span className="canvas-tag">On canvas</span>}
      </span>
      <span className="palette-label">{template.label}</span>
      {pricing && (
        <span className="suggestion-tree" aria-hidden={false}>
          <span className="suggestion-node" style={{ borderLeftColor: nodeAccent(template.nodeType) }}>
            <span className="suggestion-kind">Layer</span>
            <span>{template.label}</span>
          </span>
          <span className="suggestion-stem" aria-hidden="true" />
          <span className="suggestion-node" style={{ borderLeftColor: nodeAccent('PRICING_OUTPUT') }}>
            <span className="suggestion-kind">Pricing Output</span>
            <span>{pricing.label || 'Pricing'}</span>
          </span>
        </span>
      )}
    </div>
  );
}
