"use client";

import { OptionTypeBadge } from "@/components/shared/badges/OptionTypeBadge";
import { OrderSideBadge } from "@/components/shared/badges/OrderSideBadge";
import type { OptionRight, OrderSide } from "@/lib/strategy-builder/types";

export function LegRightToggle({
  value,
  onChange,
}: {
  value: OptionRight;
  onChange: (right: OptionRight) => void;
}) {
  return <OptionTypeBadge right={value} onToggle={onChange} />;
}

export function LegSideToggle({
  value,
  onChange,
}: {
  value: OrderSide;
  onChange: (side: OrderSide) => void;
}) {
  return <OrderSideBadge side={value} onToggle={onChange} />;
}
