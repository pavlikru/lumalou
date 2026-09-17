// Command builders. Each returns the app-level payload: [opcode] + args.
import { COMMANDS } from "./generated.js";

const NO_MODIFY = 0x0f;
const u8 = (...a: number[]) => new Uint8Array(a);

export const bcd = (n: number | null): number =>
  n == null ? 0xff : ((Math.floor(n / 10) << 4) | (n % 10)) & 0xff;

export function reduceLowNibbles(values: number[]): Uint8Array {
  let v = values.slice();
  if (v.length % 2) v = [0, ...v];
  const out = new Uint8Array(v.length / 2);
  for (let i = 0; i < v.length; i += 2) out[i / 2] = ((v[i] << 4) | (v[i + 1] & 0x0f)) & 0xff;
  return out;
}

// light
export const setLightColor = (c: number) => u8(COMMANDS.SET_LIGHT_COLOR, c);
export const setBrightness = (l: number) => u8(COMMANDS.SET_LED_BRIGHTNESS, l & 0xff);
export const setLightDuration = (d: number) => u8(COMMANDS.SET_SOOTHER_MODE_LIGHT_DURATION, d);
export const turnOffBacklight = () => u8(COMMANDS.TURN_OFF_CLOUD_BACKLIGHT);

// audio
export const playAudio = (a: number) => u8(COMMANDS.PLAY_AUDIO, a);
export const turnOffAudio = () => u8(COMMANDS.TURN_OFF_AUDIO);
export const setPlaylistDuration = (d: number) => u8(COMMANDS.SET_PLAYLIST_DURATION, d);
export function setMusicPlaylist(songIds: number[]): Uint8Array {
  const ids = songIds.filter((s) => s !== 0).slice(0, 12);
  while (ids.length < 12) ids.push(0);
  return new Uint8Array([COMMANDS.SET_MUSIC_PLAYLIST, ...ids]);
}

// volume
export const setVolume = (l: number) => u8(COMMANDS.SET_VOLUME, l & 0xff);
export const setRoutineVolume = (l: number) => u8(COMMANDS.SET_ROUTINE_MODE_VOLUME, l & 0xff);

// system
export const setGlobalOn = (on: boolean) => u8(COMMANDS.SET_GLOBAL_ON, on ? 1 : 0);
export function setGlobalState(o: {
  lightsOn?: number; brightness?: number; musicOn?: number; volume?: number;
  r2r?: number; r2rAlarm?: number; napAlarm?: number; routine?: number;
} = {}): Uint8Array {
  const f = (x?: number) => (x == null ? NO_MODIFY : x & 0x0f);
  return new Uint8Array([COMMANDS.SET_GLOBAL_STATE, ...reduceLowNibbles(
    [f(o.lightsOn), f(o.brightness), f(o.musicOn), f(o.volume), f(o.r2r), f(o.r2rAlarm), f(o.napAlarm), f(o.routine)])]);
}
export const setCurrentDate = (h: number, m: number, s: number, wd: number) =>
  u8(COMMANDS.SET_CURRENT_DATE, bcd(h), bcd(m), bcd(s), bcd(wd));
export function setClockSettings(displayOn: boolean, brightness: number, fmt: number): Uint8Array {
  return new Uint8Array([COMMANDS.SET_CLOCK_SETTINGS, ...reduceLowNibbles([0, displayOn ? 1 : 0, brightness & 0x0f, fmt])]);
}

// timers / routine
export const setR2RStatus = (on: boolean) => u8(COMMANDS.SET_R2R_STATUS, on ? 1 : 0);
export const startNap = (d: number) => u8(COMMANDS.START_NAP_TIME, d);
export const setNapAlarm = (a: number) => u8(COMMANDS.SET_NAP_TIME_ALARM, a);
export const setRoutineStatus = (on: boolean) => u8(COMMANDS.SET_ROUTINE_MODE_STATUS, on ? 1 : 0);
export const startRoutineMode = () => u8(COMMANDS.START_ROUTINE_MODE);
export const routineControl = (c: number) => u8(COMMANDS.ROUTINE_CONTROL_COMMAND, c);

// read-only queries
const REQUESTS: Record<string, keyof typeof COMMANDS> = {
  global_state: "REQUEST_GLOBAL_STATE", current_date: "REQUEST_CURRENT_DATE",
  toyic_fw_version: "REQUEST_TOYIC_FW_VERSION", led_brightness: "REQUEST_LED_BRIGHTNESS",
  light_color: "REQUEST_LIGHT_COLOR", light_duration: "REQUEST_SOOTHER_MODE_LIGHT_DURATION",
  volume: "REQUEST_VOLUME", routine_volume: "REQUEST_ROUTINE_MODE_VOLUME",
  song_playing: "REQUEST_SONG_PLAYING", music_playlist: "REQUEST_MUSIC_PLAYLIST",
  playlist_duration: "REQUEST_PLAYLIST_DURATION", operation_mode: "REQUEST_OPERATION_MODE",
  activity_state: "REQUEST_ACTIVITY_STATE", current_stage: "REQUEST_CURRENT_STAGE",
  clock_settings: "REQUEST_CLOCK_SETTINGS", transmission_mode: "REQUEST_TRANSMISSION_MODE",
  routine_mode_status: "REQUEST_ROUTINE_MODE_STATUS", routine_task_status: "REQUEST_ROUTINE_TASK_STATUS",
  routine_music_status: "REQUEST_ROUTINE_MUSIC_STATUS", r2r_status: "REQUEST_R2R_STATUS",
  r2r_times: "REQUEST_R2R_TIMES", sleepy_times: "REQUEST_SLEEPY_TIMES",
  r2r_alarm_status: "REQUEST_R2R_ALARM_STATUS", r2r_alarms: "REQUEST_R2R_ALARMS",
  nap_current_status: "REQUEST_CURRENT_NAP_TIME_STATUS", nap_alarm_status: "REQUEST_NAP_TIME_ALARM_STATUS",
  nap_alarm: "REQUEST_NAP_TIME_ALARM", time_prescaler: "REQUEST_TIME_PRESCALER",
};
export function request(name: string): Uint8Array {
  if (!Object.prototype.hasOwnProperty.call(REQUESTS, name)) {
    throw new Error(`unknown request name "${name}"`);
  }
  return u8(COMMANDS[REQUESTS[name]]);
}

// transport: enable the toy-IC to stream responses (raw SSI0 ENABLE_RX)
export const ENABLE_RX = u8(0x01, 0x50, 0x01);
