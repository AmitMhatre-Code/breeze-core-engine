import * as React from "react";
const SVGComponent = (props) => (
  <svg
    width="800px"
    height="800px"
    viewBox="0 0 48 48"
    xmlns="http://www.w3.org/2000/svg"
    enableBackground="new 0 0 48 48"
    {...props}
  >
    <g fill="#1565C0">
      <polygon points="43.4,13 35,20 35,6" />
      <rect x={4} y={11} width={34} height={4} />
    </g>
    <g fill="#2196F3">
      <rect x={40} y={23} width={4} height={19} />
      <rect x={34} y={23} width={4} height={19} />
      <rect x={28} y={23} width={4} height={19} />
      <rect x={22} y={23} width={4} height={19} />
      <rect x={16} y={23} width={4} height={19} />
      <rect x={10} y={23} width={4} height={19} />
      <rect x={4} y={23} width={4} height={19} />
    </g>
  </svg>
);
export default SVGComponent;
