import process from 'node:process';
import { URL } from 'node:url';
import { Client, GatewayIntentBits } from 'discord.js';
import { loadEvents } from './util/loaders.ts';

const client = new Client({
	intents: [
		GatewayIntentBits.Guilds,
		GatewayIntentBits.GuildMembers,
		GatewayIntentBits.GuildMessages,
		GatewayIntentBits.GuildExpressions,
		GatewayIntentBits.GuildVoiceStates,
		GatewayIntentBits.GuildPresences,
		GatewayIntentBits.GuildMessages,
		GatewayIntentBits.GuildMessageReactions,
		GatewayIntentBits.MessageContent,
	],
});

const events = await loadEvents(new URL('events/', import.meta.url));

for (const event of events) {
	client[event.once ? 'once' : 'on'](event.name, async (...args) => {
		try {
			await event.execute(...args);
		} catch (error) {
			console.error(`Error executing event ${String(event.name)}:`, error);
		}
	});
}

void client.login(process.env.DISCORD_TOKEN);
