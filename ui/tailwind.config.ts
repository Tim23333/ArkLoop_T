import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        root: '#0B0F13',
        panel: '#11161B',
        sidebar: '#11161B',
        'sidebar-end': '#080C10',
        'title-bar': '#10151A',
        'timeline-top': '#11161B',
        'timeline-bg': '#13181D',
        'timeline-bg-end': '#0A0E12',
        track: '#4D5156',
        'track-start': '#676B70',
        'track-stroke': '#777C83',
        'text-primary': '#D5D9DE',
        'text-muted': '#A6ADB5',
        'text-dim': '#7F8790',
        'border-panel': '#20262D',
        'grid-dark': '#222A31',
        'grid-light': '#252B32',
        tick: '#59616A',
        divider: '#1D232A',
        workspace: '#14181C',
        'accent-blue': '#49B8FF',
        'accent-red': '#FF3B36',
        'accent-green': '#35C64A',
        'accent-yellow': '#E8B83C',
        'playhead-fill': '#4AA3D8',
        'playhead-line': '#6FC6FF',
        highlight: '#303841',
      },
      fontFamily: {
        ui: ['-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Microsoft YaHei', 'Arial', 'sans-serif'],
      },
      width: {
        'sidebar': '224px',
        'timeline-left': '220px',
      },
      height: {
        'title': '32px',
        'sidebar': '435px',
        'timeline': '232px',
        'timeline-topbar': '43px',
      },
    },
  },
  plugins: [],
}

export default config
