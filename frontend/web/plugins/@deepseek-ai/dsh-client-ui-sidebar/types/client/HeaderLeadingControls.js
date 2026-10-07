import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/** macOS-desktop conversation-header controls for the fully hidden sidebar. */
import { IconNewChatOutline16, IconPanelLeftOutline16, isDarwinDesktop, Tooltip, } from '@deepseek-ai/dsh-client-ui-primitives';
import css from './HeaderLeadingControls.module.css';
/**
 * Sidebar-open and New Session controls in the conversation header's leading
 * seat. On macOS desktop a collapsed sidebar hides entirely (no rail), taking
 * both controls off screen; this occupant puts them back beside the traffic
 * lights. Mounted whenever the platform matches; visibility rides the
 * AppFrame-published `data-sidebar-collapsed` attribute in CSS, so no
 * collapse-state pipe is added here.
 * @param props - Injected sidebar actions plus the sidebar locale seat.
 * @returns the two header controls, or null off macOS desktop.
 */
export function HeaderLeadingControls({ toggleSidebar, startSession, t }) {
    if (!isDarwinDesktop())
        return null;
    return (_jsxs("div", { className: css.controls, children: [_jsx(Tooltip, { label: t('toggle.open'), delayMs: 500, children: _jsx("button", { type: "button", className: css.iconButton, "aria-label": t('toggle.open'), onClick: () => { toggleSidebar(); }, children: _jsx(IconPanelLeftOutline16, { size: 16 }) }) }), _jsx(Tooltip, { label: t('session.new.label'), delayMs: 500, children: _jsx("button", { type: "button", className: css.iconButton, "aria-label": t('session.new.label'), onClick: () => { startSession(); }, children: _jsx(IconNewChatOutline16, { size: 16 }) }) })] }));
}
//# sourceMappingURL=HeaderLeadingControls.js.map