import React, { useState, useEffect, useRef } from 'react';
import Head from 'next/head';
import TopBar from '../components/operations/TopBar';
import LeftPanel from '../components/operations/LeftPanel';
import LiveMap from '../components/operations/LiveMap';
import AipStaffPanel from '../components/operations/AipStaffPanel';
import BottomPanel from '../components/operations/BottomPanel';

export default function OperationsCenter() {
  const [gameState, setGameState] = useState(null);
  const [connected, setConnected] = useState(false);
  const [battlefields, setBattlefields] = useState([]);
  const [info, setInfo] = useState(null);
  const ws = useRef(null);

  useEffect(() => {
    // Connect to FastAPI WebSocket
    ws.current = new WebSocket('ws://localhost:8000/ws/command');

    ws.current.onopen = () => {
      setConnected(true);
      // pull the battlefield catalog so the operator can pick a theatre
      ws.current.send(JSON.stringify({ type: 'LIST_BATTLEFIELDS' }));
    };

    ws.current.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'STATE_UPDATE') {
          setGameState(msg.data);
        } else if (msg.type === 'BATTLEFIELDS') {
          setBattlefields(msg.data);
        } else if (msg.type === 'INFO') {
          setInfo(msg.message);
        }
      } catch (e) {
        console.error("WS parse error", e);
      }
    };
    
    ws.current.onclose = () => {
      setConnected(false);
    };

    return () => {
      if (ws.current) ws.current.close();
    };
  }, []);

  const sendCommand = (cmd) => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify(cmd));
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-900 text-gray-100 overflow-hidden font-sans">
      <Head>
        <title>Dexia AIP Command Center</title>
      </Head>

      {/* Top Bar */}
      <div className="h-14 border-b border-gray-700 bg-gray-800 shrink-0 z-10">
        <TopBar connected={connected} gameState={gameState} />
      </div>

      <div className="flex flex-1 overflow-hidden relative">
        {/* Left Panel: Setup & Assets */}
        <div className="w-80 border-r border-gray-700 bg-gray-800/90 flex flex-col shrink-0 z-10 shadow-xl overflow-y-auto">
          <LeftPanel sendCommand={sendCommand} gameState={gameState}
                     battlefields={battlefields} info={info} />
        </div>

        {/* Center: Live Map */}
        <div className="flex-1 relative bg-black">
          <LiveMap gameState={gameState} sendCommand={sendCommand} />
        </div>

        {/* Right Panel: AIP Feed & Approvals */}
        <div className="w-96 border-l border-gray-700 bg-gray-800/90 flex flex-col shrink-0 z-10 shadow-xl overflow-y-auto">
          <AipStaffPanel gameState={gameState} sendCommand={sendCommand} />
        </div>
      </div>

      {/* Bottom Panel: Execution Controls */}
      <div className="h-20 border-t border-gray-700 bg-gray-800 shrink-0 z-10">
        <BottomPanel gameState={gameState} sendCommand={sendCommand} />
      </div>
    </div>
  );
}
