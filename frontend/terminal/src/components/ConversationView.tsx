import React from 'react';
import {Box, Text} from 'ink';

import {useTheme} from '../theme/ThemeContext.js';
import type {TranscriptItem} from '../types.js';
import {MarkdownText} from './MarkdownText.js';
import {ToolCallDisplay} from './ToolCallDisplay.js';
import {WelcomeBanner} from './WelcomeBanner.js';

type ToolPair = readonly [TranscriptItem, TranscriptItem];
type GroupedItem = TranscriptItem | ToolPair;

function groupToolPairs(items: TranscriptItem[]): GroupedItem[] {
	const result: GroupedItem[] = [];
	let i = 0;
	while (i < items.length) {
		const cur = items[i];
		const next = items[i + 1];
		if (cur.role === 'tool' && next?.role === 'tool_result') {
			result.push([cur, next] as const);
			i += 2;
		} else {
			result.push(cur);
			i++;
		}
	}
	return result;
}

// Keep the visible transcript small enough that Ink's full-screen redraw
// (log-update erases and rewrites every line each render) stays imperceptible.
// slice(-N) by message count is wrong: 40 messages render to 100+ terminal
// lines, and every assistant flush then rewrites all 100 lines -> flicker.
const MAX_VISIBLE_LINES = 20;

function estimateItemLines(item: TranscriptItem): number {
	const base = Math.max(1, (item.text ?? '').split('\n').length);
	switch (item.role) {
		case 'assistant':
		case 'reasoning':
			return base + 1; // icon row + body (markdown can expand further)
		case 'tool':
		case 'tool_result':
			return Math.min(base, 4) + 1; // collapse long tool output
		case 'system':
		case 'status':
		case 'log':
			return 1;
		default:
			return base;
	}
}

function sliceToLineLimit(items: TranscriptItem[], maxLines: number): TranscriptItem[] {
	let lines = 0;
	let start = items.length;
	for (let i = items.length - 1; i >= 0; i--) {
		const estimated = estimateItemLines(items[i]!);
		if (lines + estimated > maxLines) {
			break;
		}
		lines += estimated;
		start = i;
	}
	return items.slice(start);
}

function ConversationViewInner({
	items,
	assistantBuffer,
	reasoningBuffer,
	showWelcome,
	outputStyle,
}: {
	items: TranscriptItem[];
	assistantBuffer: string;
	reasoningBuffer?: string;
	showWelcome: boolean;
	outputStyle: string;
}): React.JSX.Element {
	const {theme} = useTheme();
	const isCodexStyle = outputStyle === 'codex';
	const visible = sliceToLineLimit(items, MAX_VISIBLE_LINES);
	const grouped = groupToolPairs(visible);

	return (
		<Box flexDirection="column" flexGrow={1}>
			{showWelcome && items.length === 0 ? <WelcomeBanner /> : null}

			{grouped.map((group, index) => {
				if (Array.isArray(group)) {
					const [toolItem, resultItem] = group as [TranscriptItem, TranscriptItem];
					return (
						<ToolCallDisplay
							key={index}
							item={toolItem}
							resultItem={resultItem}
							outputStyle={outputStyle}
						/>
					);
				}
				return (
					<MessageRow
						key={index}
						item={group as TranscriptItem}
						theme={theme}
						outputStyle={outputStyle}
					/>
				);
			})}

			{reasoningBuffer ? (
				<Box marginTop={1} flexDirection="column">
					<Text dimColor bold>{'🧠'}</Text>
					<Box marginLeft={2} flexDirection="column">
						<Text dimColor italic>{reasoningBuffer}</Text>
					</Box>
				</Box>
			) : null}

			{assistantBuffer ? (
				isCodexStyle ? (
					<Box flexDirection="row" marginTop={0}>
						<Text>{assistantBuffer}</Text>
					</Box>
				) : (
					<Box marginTop={1} marginBottom={0} flexDirection="column">
						<Text>
							<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
						</Text>
						<Box marginLeft={2} flexDirection="column">
							<MarkdownText content={assistantBuffer} />
						</Box>
					</Box>
				)
			) : null}
		</Box>
	);
}

export const ConversationView = React.memo(ConversationViewInner);

function MessageRow({
	item,
	theme,
	outputStyle,
}: {
	item: TranscriptItem;
	theme: ReturnType<typeof useTheme>['theme'];
	outputStyle: string;
}): React.JSX.Element {
	const isCodexStyle = outputStyle === 'codex';

	switch (item.role) {
		case 'user':
			if (isCodexStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text dimColor>{'> '}</Text>
							<Text>{item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={1} marginBottom={0}>
					<Text>
						<Text color={theme.colors.secondary} bold>{theme.icons.user}</Text>
						<Text>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'assistant':
			if (isCodexStyle) {
				return (
					<Box marginTop={0} marginBottom={0}>
						<Text>{item.text}</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={1} marginBottom={0} flexDirection="column">
					<Text>
						<Text color={theme.colors.success} bold>{theme.icons.assistant}</Text>
					</Text>
					<Box marginLeft={2} flexDirection="column">
						<MarkdownText content={item.text} />
					</Box>
				</Box>
			);

		case 'tool':
		case 'tool_result':
			return <ToolCallDisplay item={item} outputStyle={outputStyle} />;

		case 'system':
			if (isCodexStyle) {
				return (
					<Box marginTop={0}>
						<Text>
							<Text color={theme.colors.warning}>[system]</Text>
							<Text> {item.text}</Text>
						</Text>
					</Box>
				);
			}
			return (
				<Box marginTop={0}>
					<Text>
						<Text color={theme.colors.warning}>{theme.icons.system}</Text>
						<Text color={theme.colors.warning}>{item.text}</Text>
					</Text>
				</Box>
			);

		case 'reasoning':
			return (
				<Box marginTop={0} flexDirection="column">
					<Text dimColor bold>{'🧠'}</Text>
					<Box marginLeft={2} flexDirection="column">
						<Text dimColor italic>{item.text}</Text>
					</Box>
				</Box>
			);

		case 'status':
			return (
				<Box marginTop={0}>
					<Text color={theme.colors.info}>{item.text}</Text>
				</Box>
			);

		case 'log':
			return (
				<Box>
					<Text dimColor>{item.text}</Text>
				</Box>
			);

		default:
			return (
				<Box>
					<Text>{item.text}</Text>
				</Box>
			);
	}
}
