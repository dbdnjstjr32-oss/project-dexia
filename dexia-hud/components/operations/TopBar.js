import React from 'react';

export default function TopBar({ connected, gameState }) {
  return (
    <div className="flex items-center justify-between px-6 h-full text-sm font-semibold tracking-wide">
      <div className="flex items-center space-x-4">
        <span className="text-cyan-400">MISSION: OPERATION VANGUARD</span>
        <span className="text-gray-500">|</span>
        <span className="text-gray-300">
          TICK: {gameState ? gameState.tick : '000'}
        </span>
      </div>

      <div className="flex items-center space-x-6">
        <div className="flex items-center space-x-2">
          <span className="text-gray-400">BLUE FORCE</span>
          <span className="text-blue-400 px-2 py-1 bg-blue-900/30 rounded">{gameState ? gameState.blue.length : 0}</span>
        </div>
        <div className="flex items-center space-x-2">
          <span className="text-gray-400">EST. RED FORCE</span>
          <span className="text-red-400 px-2 py-1 bg-red-900/30 rounded">{gameState ? gameState.tracks.length : '?'}</span>
        </div>
      </div>

      <div className="flex items-center space-x-4">
        <div className="flex items-center space-x-2">
          <span className="text-gray-400">AIP STATUS:</span>
          {gameState && gameState.paused ? (
            <span className="text-amber-400 animate-pulse">PENDING APPROVAL</span>
          ) : (
            <span className="text-emerald-400">ACTIVE</span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <span className="text-gray-400">COMM:</span>
          <span className={connected ? 'text-emerald-400' : 'text-rose-500'}>
            {connected ? 'SECURE' : 'OFFLINE'}
          </span>
        </div>
      </div>
    </div>
  );
}
