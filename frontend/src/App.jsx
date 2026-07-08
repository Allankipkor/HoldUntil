import React, { useState, useEffect, useRef } from 'react';
import { 
  ShieldCheck, Smartphone, Gavel, Settings, Search, Send, 
  CheckCircle2, AlertTriangle, Trash2, UserCheck, RefreshCw, 
  FileText, Image, ArrowRight, Lock, PlusCircle, DollarSign, 
  Truck, HelpCircle, ShieldAlert, Award
} from 'lucide-react';

const BACKEND_URL = ''; // Proxied via Vite

export default function App() {
  const [activeTab, setActiveTab] = useState('sandbox');
  
  // Public Verification State
  const [searchSellerId, setSearchSellerId] = useState('');
  const [sellerVerifyResult, setSellerVerifyResult] = useState(null);
  const [verifyLoading, setVerifyLoading] = useState(false);
  const [verifyError, setVerifyError] = useState('');

  // Sandbox State
  const [sellerPhone, setSellerPhone] = useState('254711111111');
  const [buyerPhone, setBuyerPhone] = useState('254722222222');
  const [sellerMsg, setSellerMsg] = useState('');
  const [buyerMsg, setBuyerMsg] = useState('');
  const [chatLogs, setChatLogs] = useState([]);
  const [activeDealId, setActiveDealId] = useState(null);
  const [dealDetails, setDealDetails] = useState(null);
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const [evidenceTrackingNumber, setEvidenceTrackingNumber] = useState('SDY-9922');
  const [videoCallDuration, setVideoCallDuration] = useState(30);
  const [videoCallBuyerPresent, setVideoCallBuyerPresent] = useState(true);
  const [videoCallProxyName, setVideoCallProxyName] = useState('');
  const [videoCallMilestoneIndex, setVideoCallMilestoneIndex] = useState('');
  
  // Dashboard State
  const [disputes, setDisputes] = useState([]);
  const [selectedDisputeId, setSelectedDisputeId] = useState(null);
  const [selectedDisputeDetails, setSelectedDisputeDetails] = useState(null);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [moderatorReasoning, setModeratorReasoning] = useState('');
  const [modOutcome, setModOutcome] = useState('release');
  const [modSplitPct, setModSplitPct] = useState(50);
  const [resolverId, setResolverId] = useState('MOD_1');
  const [metrics, setMetrics] = useState({ total_disputes: 0, ai_agreement_rate_pct: 100.0 });
  const [checklist, setChecklist] = useState({
    codeMatch: false,
    timelineValid: false,
    descMatch: false,
    transcriptVerified: false
  });
  const [showBuyerConfirmDeclaration, setShowBuyerConfirmDeclaration] = useState(false);

  // Admin State
  const [users, setUsers] = useState([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [systemSettings, setSystemSettings] = useState({
    MIN_TRADES_FOR_PROFILE_STATS: 3,
    MIN_DEALS_FOR_BADGE: 10
  });
  const [settingsLoading, setSettingsLoading] = useState(false);

  // Poll chat logs periodically when in sandbox deal
  useEffect(() => {
    if (!activeDealId) return;
    const interval = setInterval(() => {
      fetchDealDetails(activeDealId);
    }, 3000);
    return () => clearInterval(interval);
  }, [activeDealId]);

  // Load disputes queue on active tab change
  useEffect(() => {
    if (activeTab === 'moderator') {
      fetchDisputes();
      fetchMetrics();
    } else if (activeTab === 'admin') {
      fetchUsers();
      fetchSettings();
    }
  }, [activeTab]);

  const fetchMetrics = async () => {
    try {
      const res = await fetch('/api/dashboard/metrics');
      if (res.ok) {
        const data = await res.json();
        setMetrics(data);
      }
    } catch (e) {
      console.warn("Failed to fetch system metrics.");
    }
  };

  // ----------------------------------------------------
  // API INTERACTIONS
  // ----------------------------------------------------

  const fetchDisputes = async () => {
    setDashboardLoading(true);
    try {
      const res = await fetch('/api/dashboard/disputes');
      if (res.ok) {
        const data = await res.json();
        setDisputes(data);
      }
    } catch (e) {
      console.warn("Using offline fallback data for disputes queue.");
      // Fallback Mock Data
      setDisputes([
        {
          id: "disp_991",
          deal_id: "deal_101",
          item_description: "iPhone 13 Pro Max - Blue",
          agreed_price: 75000,
          filed_by_handle: "254722222222",
          seller_handle: "254711111111",
          buyer_handle: "254722222222",
          reason: "Received an empty box filled with stone. Photo proof uploaded.",
          tier: "tier_2_ai",
          ai_decision: "refund",
          ai_reasoning: "Seller uploaded delivery photo, but imagehash matches a photo submitted in deal_045. Highly likely to be photo recycling fraud. EXIF data metadata has also been removed. Recommending complete refund to the buyer.",
          ai_confidence: 0.94,
          created_at: new Date().toISOString()
        }
      ]);
    } finally {
      setDashboardLoading(false);
    }
  };

  const fetchUsers = async () => {
    setUsersLoading(true);
    try {
      const res = await fetch('/api/dashboard/users');
      if (res.ok) {
        const data = await res.json();
        setUsers(data);
      }
    } catch (e) {
      console.warn("Using offline fallback data for users list.");
      setUsers([
        { id: "usr_seller_1", phone_or_handle: "254711111111", trust_score: 98.0, dispute_win_rate: 1.0, role: "user", created_at: "2026-01-01" },
        { id: "usr_buyer_1", phone_or_handle: "254722222222", trust_score: 100.0, dispute_win_rate: 0.0, role: "user", created_at: "2026-03-01" },
      ]);
    } finally {
      setUsersLoading(false);
    }
  };

  const fetchSettings = async () => {
    setSettingsLoading(true);
    try {
      const res = await fetch('/api/dashboard/settings');
      if (res.ok) {
        const data = await res.json();
        setSystemSettings(data);
      }
    } catch (e) {
      console.warn("Failed to fetch settings from API.");
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleUpdateSettings = async (e) => {
    e.preventDefault();
    try {
      const formData = new FormData();
      formData.append("min_trades", systemSettings.MIN_TRADES_FOR_PROFILE_STATS);
      formData.append("min_deals_for_badge", systemSettings.MIN_DEALS_FOR_BADGE);
      
      const res = await fetch('/api/dashboard/settings', {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        const data = await res.json();
        setSystemSettings(data.settings);
        alert("Escrow configuration updated successfully!");
      } else {
        alert("Failed to update escrow configuration.");
      }
    } catch (err) {
      alert("Error updating escrow configuration.");
    }
  };

  const handleVerifySeller = async (e) => {
    e.preventDefault();
    if (!searchSellerId) return;
    setVerifyLoading(true);
    setVerifyError('');
    setSellerVerifyResult(null);

    try {
      const res = await fetch(`/api/users/${searchSellerId}/profile`);
      if (res.ok) {
        const data = await res.json();
        setSellerVerifyResult(data);
      } else {
        setVerifyError("User profile not found. Verify you entered the complete ID or handle.");
      }
    } catch (err) {
      console.warn("Offline verification simulation.");
      // Fallback
      if (searchSellerId.length > 5) {
        setSellerVerifyResult({
          phone_or_handle: searchSellerId,
          is_new_trader: false,
          has_badge: true,
          completed_trades: 18,
          completion_rate: 97.06,
          positive_rate: 100.0,
          trust_score: 98.0,
          display_text: `${searchSellerId} [PRO]\nTrades: 18 Trades (97.06%) | 👍 100.00%`
        });
      } else {
        setVerifyError("User not found. Try entering a mock handle: '254711111111'");
      }
    } finally {
      setVerifyLoading(false);
    }
  };

  const selectDispute = async (id) => {
    setSelectedDisputeId(id);
    try {
      const disp = disputes.find(d => d.id === id);
      if (!disp) return;
      const res = await fetch(`/api/dashboard/deals/${disp.deal_id}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedDisputeDetails(data);
      } else {
        mockDisputeDetails(disp);
      }
    } catch (e) {
      mockDisputeDetails(disputes.find(d => d.id === id));
    }
  };

  const mockDisputeDetails = (disp) => {
    setSelectedDisputeDetails({
      deal: {
        id: disp.deal_id,
        seller_handle: disp.seller_handle,
        buyer_handle: disp.buyer_handle,
        item_description: disp.item_description,
        agreed_price: disp.agreed_price,
        delivery_deadline: new Date(Date.now() + 86400000).toISOString(),
        status: "disputed",
        verification_code: "HU-8F9K",
        courier_name: "Sendy",
        tracking_number: "SDY-9988",
        seller_confirmed: true,
        buyer_confirmed: true
      },
      payments: [{ amount: disp.agreed_price, status: "paid", stk_push_ref: "ws_co_stk123" }],
      evidences: [{
        submitted_by_handle: "Seller",
        file_url: "/simulated/media/package_reused.jpg",
        perceptual_hash: "hash_duplicate_reused_12345",
        exif_data: { Make: "Apple", Model: "iPhone 13", DateTime: "2026-07-04" },
        dynamic_code_detected: true,
        courier_verified: false
      }],
      chat_logs: [
        { sender_handle: "Seller", message_content: "Hey, setting up deal", timestamp: new Date(Date.now() - 3600000).toISOString() },
        { sender_handle: "Bot", message_content: "Consent notice displayed...", timestamp: new Date(Date.now() - 3500000).toISOString() },
        { sender_handle: "Buyer", message_content: "Confirmed, sent M-Pesa code", timestamp: new Date(Date.now() - 3400000).toISOString() },
        { sender_handle: "Seller", message_content: "Shipped package, tracking SDY-9988", timestamp: new Date(Date.now() - 3200000).toISOString() },
        { sender_handle: "Buyer", message_content: "The package arrived but it is just stones! You scammed me", timestamp: new Date(Date.now() - 1200000).toISOString() }
      ],
      disputes: [disp]
    });
  };

  const handleResolveDispute = async (e) => {
    e.preventDefault();
    if (!moderatorReasoning) return alert("Please specify reasoning before submitting.");
    
    try {
      const formData = new FormData();
      formData.append("outcome", modOutcome);
      formData.append("partial_split_percentage", modSplitPct);
      formData.append("reasoning", moderatorReasoning);
      formData.append("resolver_id", resolverId);

      const res = await fetch(`/api/dashboard/disputes/${selectedDisputeId}/resolve`, {
        method: 'POST',
        body: formData
      });

      if (res.ok) {
        alert("Dispute resolved successfully!");
        fetchDisputes();
        setSelectedDisputeId(null);
        setSelectedDisputeDetails(null);
        setModeratorReasoning('');
      }
    } catch (e) {
      alert("Resolution simulated successfully!");
      // Simulate locally
      setDisputes(prev => prev.filter(d => d.id !== selectedDisputeId));
      setSelectedDisputeId(null);
      setSelectedDisputeDetails(null);
      setModeratorReasoning('');
    }
  };

  const handleOverrideTrust = async (userId, newScore) => {
    try {
      const formData = new FormData();
      formData.append("trust_score", newScore);
      const res = await fetch(`/api/dashboard/users/${userId}/override`, {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        alert("Trust score updated.");
        fetchUsers();
      }
    } catch (e) {
      setUsers(prev => prev.map(u => u.id === userId ? { ...u, trust_score: parseFloat(newScore) } : u));
    }
  };

  const handleResetAll = async () => {
    if (!window.confirm("Are you sure you want to delete all database transactions and reset all active chat sessions? This cannot be undone.")) return;
    try {
      const res = await fetch('/api/dashboard/simulation/reset-all', {
        method: 'POST'
      });
      if (res.ok) {
        alert("Database and chat sessions cleared successfully!");
        setChatLogs([]);
        setActiveDealId(null);
        setDealDetails(null);
        setDisputes([]);
        setUsers([]);
        setActiveTab('sandbox');
      } else {
        alert("Reset failed.");
      }
    } catch (e) {
      alert("Reset simulation complete.");
    }
  };

  // ----------------------------------------------------
  // SANDBOX SIMULATOR ACTIONS
  // ----------------------------------------------------

  const sendSandboxMessage = async (senderPhone, msgText, roleLabel) => {
    if (!msgText.trim()) return;
    setSandboxLoading(true);
    
    // Add message locally first for responsive UI
    const newUserLog = {
      sender_handle: roleLabel,
      message_content: msgText,
      timestamp: new Date().toISOString()
    };
    setChatLogs(prev => [...prev, newUserLog]);

    try {
      const formData = new FormData();
      formData.append("phone_or_handle", senderPhone);
      formData.append("message", msgText);
      formData.append("platform", "whatsapp");

      const res = await fetch('/api/dialogue', {
        method: 'POST',
        body: formData
      });

      if (res.ok) {
        const data = await res.json();
        
        // Bot replied
        const botLog = {
          sender_handle: "Bot",
          message_content: data.reply,
          timestamp: new Date().toISOString()
        };
        setChatLogs(prev => [...prev, botLog]);

        if (data.deal_id) {
          setActiveDealId(data.deal_id);
          fetchDealDetails(data.deal_id);
        } else {
          const cleanMsg = msgText.trim().toUpperCase();
          if (cleanMsg === "CANCEL" || cleanMsg === "RESET" || cleanMsg === "SELL") {
            setActiveDealId(null);
            setDealDetails(null);
            setShowBuyerConfirmDeclaration(false);
            if (cleanMsg === "SELL") {
              setChatLogs([
                newUserLog,
                {
                  sender_handle: "Bot",
                  message_content: data.reply,
                  timestamp: new Date().toISOString()
                }
              ]);
            } else {
              setTimeout(() => {
                setChatLogs([]);
              }, 1500);
            }
          }
        }
      } else {
        console.warn("Server responded with error, falling back to simulator");
        simulateOfflineResponse(msgText, roleLabel);
      }
    } catch (e) {
      // Offline fallback Dialogue engine simulator
      simulateOfflineResponse(msgText, roleLabel);
    } finally {
      setSandboxLoading(false);
    }
  };

  const simulateOfflineResponse = (msg, role) => {
    let reply = "Escrow bot simulator. Type SELL to create, or enter your join code.";
    const cleanMsg = msg.trim().toUpperCase();

    if (cleanMsg === "SELL") {
      reply = "Let's set up your secure escrow deal. What is the item description? (e.g. 'HP Pavilion Laptop, 8GB RAM, Used')";
    } else if (cleanMsg.includes("HP PAVILION") || cleanMsg.includes("IPHONE") || cleanMsg.length > 10 && cleanMsg.match(/[a-zA-Z]/)) {
      reply = "Got it. What is the agreed price in Kenyan Shillings (KES)? (Numbers only, e.g. 15000)";
    } else if (cleanMsg.match(/^\d+$/)) {
      reply = "How many days should the delivery take? (Enter a number of days, e.g. 3)";
    } else if (cleanMsg === "3" || cleanMsg === "2" || cleanMsg === "5") {
      const mockId = `deal_${Math.floor(Math.random()*1000)}`;
      setActiveDealId(mockId);
      setDealDetails({
        deal: {
          id: mockId,
          seller_handle: sellerPhone,
          buyer_handle: null,
          item_description: "HP Pavilion Laptop, 8GB RAM, Used",
          agreed_price: 15000,
          delivery_deadline: new Date(Date.now() + 259200000).toISOString(),
          status: "draft",
          seller_confirmed: true,
          buyer_confirmed: false
        },
        payments: [],
        evidences: [],
        chat_logs: []
      });
      reply = `✅ Deal Draft Created!\n\nItem: HP Pavilion Laptop\nPrice: KES 15000.00\n\nPlease forward this invite to your buyer: "Hey! Click this link to accept the escrow deal: https://wa.me/bot_number?text=JOIN_${mockId}"`;
    } else if (cleanMsg.startsWith("JOIN_")) {
      const dealId = msg.split("_")[1] || "deal_draft";
      setActiveDealId(dealId);
      setDealDetails(prev => ({
        ...prev,
        deal: {
          ...prev.deal,
          buyer_handle: buyerPhone,
          status: "awaiting_confirmation"
        }
      }));
      reply = `🤝 You are joining a secure HoldUntil escrow deal!\n\nSeller: 254711111111\nItem: HP Pavilion Laptop\nPrice: KES 15000.00\n\n📜 Consent Notice: Disputes will trigger moderator review of this transaction's transcripts.\n\nReply 'CONFIRM' to accept and trigger STK push payment.`;
    } else if (cleanMsg === "CONFIRM") {
      reply = "What is the transaction type? Reply with the number:\n1. Digital Deliverable\n2. Shipped Goods (Courier)\n3. Local In-Person Handoff\n4. Remote Physical Service";
    } else if (cleanMsg === "1" || cleanMsg === "2" || cleanMsg === "3" || cleanMsg === "4") {
      const typeMap = { "1": "digital", "2": "shipped", "3": "handoff", "4": "remote_service" };
      setDealDetails(prev => ({
        ...prev,
        deal: {
          ...prev.deal,
          transaction_type: typeMap[cleanMsg]
        }
      }));
      reply = "⚠️ IMPORTANT TRANSACTION DISCLAIMER ⚠️\n\nEvidence submitted during this transaction (photos, videos, tracking info) may be used to resolve a dispute if one arises. Please take evidence seriously and ensure it clearly and honestly reflects what actually happened — false or misleading evidence affects your trust score and may be treated as fraud.\n\nReply 'I ACKNOWLEDGE' to agree and proceed.";
    } else if (cleanMsg === "I ACKNOWLEDGE") {
      setDealDetails(prev => ({
        ...prev,
        deal: {
          ...prev.deal,
          buyer_confirmed: true,
          seller_confirmed: true,
          status: "awaiting_confirmation",
          seller_disclaimer_acknowledged: true,
          buyer_disclaimer_acknowledged: true
        }
      }));
      reply = "You have acknowledged the disclaimer. M-Pesa STK Push has been sent to your phone. Use the payment simulator below to execute.";
    } else if (cleanMsg === "YES") {
      setDealDetails(prev => ({
        ...prev,
        deal: { ...prev.deal, status: "completed" }
      }));
      reply = "🎉 Escrow released! KES 15000.00 paid out to the seller's M-Pesa account. Thank you!";
    } else if (cleanMsg === "NO") {
      setDealDetails(prev => ({
        ...prev,
        deal: { ...prev.deal, status: "disputed" }
      }));
      reply = "⚠️ Dispute filed. Escrow funds locked. What was wrong with the delivery? Type your reason.";
    } else if (cleanMsg.length > 15 && dealDetails?.deal?.status === "disputed") {
      reply = "Dispute statement recorded. Tier 2 AI moderator has started analysis. View the escalation results on the Moderator Dashboard tab.";
    }

    setTimeout(() => {
      setChatLogs(prev => [...prev, {
        sender_handle: "Bot",
        message_content: reply,
        timestamp: new Date().toISOString()
      }]);
    }, 800);
  };

  const fetchDealDetails = async (dealId) => {
    try {
      const res = await fetch(`/api/dashboard/deals/${dealId}`);
      if (res.ok) {
        const data = await res.json();
        setDealDetails(data);
        
        // Sync chat logs from backend
        const mappedLogs = data.chat_logs.map(log => ({
          sender_handle: log.sender_handle,
          message_content: log.message_content,
          media_url: log.media_url,
          is_revoked: log.is_revoked,
          id: log.id,
          timestamp: log.timestamp
        }));
        setChatLogs(mappedLogs);
      }
    } catch (e) {
      // offline
    }
  };

  const handleSimulatePayment = async () => {
    if (!dealDetails) return;
    try {
      const checkoutId = dealDetails.payments[0]?.stk_push_ref || "mock_checkout_ref";
      const formData = new FormData();
      formData.append("checkout_id", checkoutId);
      const res = await fetch('/api/dashboard/simulation/mock-mpesa-payment', {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        alert("Payment Callback Simulated Success!");
        fetchDealDetails(activeDealId);
      }
    } catch (e) {
      alert("Payment simulated successfully (offline).");
      setDealDetails(prev => ({
        ...prev,
        deal: {
          ...prev.deal,
          status: "funded",
          verification_code: "HU-8F9K"
        },
        payments: [{ amount: prev.deal.agreed_price, status: "paid", c2b_confirmation_ref: "MPESA_XYZ789" }]
      }));
      setChatLogs(prev => [...prev, 
        { sender_handle: "Bot", message_content: "💰 M-Pesa Payment of KES 15000.00 received! Escrow locked. Verification code: HU-8F9K", timestamp: new Date().toISOString() }
      ]);
    }
  };

  const handleSimulateUpload = async (photoType, options = {}) => {
    if (!dealDetails) return;
    const inAppCaptured = options.inAppCaptured !== undefined ? options.inAppCaptured : true;
    try {
      const formData = new FormData();
      formData.append("deal_id", activeDealId);
      formData.append("sender_id", dealDetails.deal.seller_id || "usr_seller_1");
      if (photoType) {
        formData.append("photo_name", photoType);
      }
      formData.append("in_app_captured", inAppCaptured ? "true" : "false");
      formData.append("captured_timestamp", new Date().toISOString());
      if (options.gps_latitude) formData.append("gps_latitude", options.gps_latitude);
      if (options.gps_longitude) formData.append("gps_longitude", options.gps_longitude);
      if (options.deliverable_file_url) formData.append("deliverable_file_url", options.deliverable_file_url);

      const res = await fetch('/api/dashboard/simulation/upload-evidence', {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        alert("Evidence Upload simulated!");
        fetchDealDetails(activeDealId);
      } else {
        const errText = await res.text();
        alert(`Failed to upload evidence: ${errText}`);
      }
    } catch (e) {
      alert(`Evidence Uploaded (${photoType || 'file'}) simulated offline.`);
      setDealDetails(prev => ({
        ...prev,
        deal: { ...prev.deal, status: "shipped" },
        evidences: [{
          submitted_by_handle: "Seller",
          file_url: photoType ? `/simulated/media/${photoType}` : (options.deliverable_file_url || ""),
          dynamic_code_detected: photoType !== "FAIL_CODE",
          perceptual_hash: photoType === "REUSE_ALERT" ? "hash_duplicate_reused_12345" : "hash_unique_99",
          courier_verified: false,
          in_app_captured: inAppCaptured,
          captured_timestamp: new Date().toISOString(),
          gps_latitude: options.gps_latitude || null,
          gps_longitude: options.gps_longitude || null,
          deliverable_file_url: options.deliverable_file_url || null
        }]
      }));
      setChatLogs(prev => [...prev, 
        { sender_handle: "Bot", message_content: `📦 Proof submitted! File: ${photoType || options.deliverable_file_url}. Prompting buyer.`, timestamp: new Date().toISOString() }
      ]);
    }
  };

  const handleSimulateVideoCall = async (duration, buyerPresent, proxyName, milestoneIndex) => {
    if (!dealDetails) return;
    try {
      const formData = new FormData();
      formData.append("deal_id", activeDealId);
      formData.append("sender_id", dealDetails.deal.seller_id || "usr_seller_1");
      formData.append("duration_seconds", duration);
      formData.append("buyer_present", buyerPresent ? "true" : "false");
      if (proxyName) formData.append("proxy_name", proxyName);
      if (milestoneIndex !== null && milestoneIndex !== undefined) {
        formData.append("milestone_index", milestoneIndex);
      }

      const res = await fetch('/api/dashboard/simulation/log-video-call', {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        alert("Video call logged successfully!");
        fetchDealDetails(activeDealId);
      } else {
        const errText = await res.text();
        alert(`Failed to log video call: ${errText}`);
      }
    } catch (e) {
      alert("Video Call simulated offline.");
      setDealDetails(prev => ({
        ...prev,
        deal: { ...prev.deal, status: milestoneIndex ? prev.deal.status : "shipped" },
        evidences: [{
          submitted_by_handle: "Seller",
          file_url: `/simulated/video_call_${milestoneIndex || 'final'}`,
          in_app_captured: true,
          captured_timestamp: new Date().toISOString(),
          video_call_log: {
            duration_seconds: duration,
            buyer_present: buyerPresent,
            proxy_name: proxyName,
            milestone_index: milestoneIndex
          }
        }]
      }));
      setChatLogs(prev => [...prev, 
        { sender_handle: "Bot", message_content: `📹 Live Video Call session logged as completion proof.`, timestamp: new Date().toISOString() }
      ]);
    }
  };

  const handleRevokeMessage = async (logId) => {
    try {
      const formData = new FormData();
      formData.append("chat_log_id", logId);
      const res = await fetch('/api/dashboard/simulation/revoke-message', {
        method: 'POST',
        body: formData
      });
      if (res.ok) {
        fetchDealDetails(activeDealId);
      }
    } catch (e) {
      setChatLogs(prev => prev.map(log => log.id === logId ? { ...log, is_revoked: true } : log));
    }
  };

  // Scroll Chat to bottom
  const chatBottomRef = useRef(null);
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatLogs]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: '100vh', background: 'var(--bg-main)' }}>
      {/* HEADER */}
      <header className="glass-card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 24px', margin: '16px', borderBottomRightRadius: '16px', borderBottomLeftRadius: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <ShieldCheck size={36} color="var(--primary)" />
          <div>
            <h1 style={{ fontSize: '1.5rem', fontFamily: 'var(--font-display)', color: 'white', display: 'flex', alignItems: 'center', gap: '8px' }}>
              HoldUntil <span style={{ fontSize: '0.75rem', padding: '2px 8px', borderRadius: '4px', background: 'rgba(6,182,212,0.2)', color: 'var(--secondary)' }}>Escrow Engine</span>
            </h1>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Secure chat-native social commerce in Kenya</p>
          </div>
        </div>
        
        {/* TABS */}
        <nav style={{ display: 'flex', gap: '8px' }}>
          <button onClick={() => setActiveTab('verify')} className={`btn ${activeTab === 'verify' ? 'btn-primary' : 'btn-secondary'}`}>
            <Search size={16} /> Seller lookup
          </button>
          <button onClick={() => setActiveTab('sandbox')} className={`btn ${activeTab === 'sandbox' ? 'btn-primary' : 'btn-secondary'}`}>
            <Smartphone size={16} /> Chat Sandbox
          </button>
          <button onClick={() => setActiveTab('moderator')} className={`btn ${activeTab === 'moderator' ? 'btn-primary' : 'btn-secondary'}`}>
            <Gavel size={16} /> Disputes Queue {disputes.length > 0 && <span style={{ background: 'var(--accent-red)', color: 'white', padding: '2px 6px', borderRadius: '50%', fontSize: '10px', marginLeft: '4px' }}>{disputes.length}</span>}
          </button>
          <button onClick={() => setActiveTab('admin')} className={`btn ${activeTab === 'admin' ? 'btn-primary' : 'btn-secondary'}`}>
            <Settings size={16} /> Admin panel
          </button>
        </nav>
      </header>

      {/* CORE VIEWPORT */}
      <main style={{ flex: 1, padding: '0 16px 24px 16px' }}>
        
        {/* PUBLIC VERIFICATION LOOKUP */}
        {activeTab === 'verify' && (
          <div style={{ maxWidth: '600px', margin: '40px auto' }} className="glass-card">
            <div style={{ padding: '32px', textAlign: 'center' }}>
              <Award size={48} color="var(--primary)" style={{ margin: '0 auto 16px auto' }} />
              <h2 style={{ fontSize: '1.75rem', marginBottom: '8px', fontFamily: 'var(--font-display)' }}>Verify HoldUntil Merchant</h2>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem', marginBottom: '24px' }}>
                Verify if a seller is certified as a Verified Safe Seller. Don't trust screenshots — confirm real-time transaction history below.
              </p>

              <form onSubmit={handleVerifySeller} style={{ display: 'flex', gap: '8px', marginBottom: '24px' }}>
                <input 
                  type="text" 
                  value={searchSellerId}
                  onChange={(e) => setSearchSellerId(e.target.value)}
                  placeholder="Enter User/Seller ID (e.g. usr_seller_1)" 
                  className="form-input"
                  style={{ flex: 1 }}
                />
                <button type="submit" className="btn btn-primary">
                  {verifyLoading ? 'Searching...' : 'Lookup'}
                </button>
              </form>

              {verifyError && <p style={{ color: 'var(--accent-red)', fontSize: '0.875rem' }}>{verifyError}</p>}

              {sellerVerifyResult && (
                <div style={{ borderTop: '1px solid var(--border-muted)', paddingTop: '24px', textAlign: 'left' }}>
                  
                  {/* Handle & Badge Header */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '16px' }}>
                    <h3 style={{ fontSize: '1.25rem', margin: 0, fontWeight: 'bold' }}>
                      {sellerVerifyResult.phone_or_handle}
                    </h3>
                    {sellerVerifyResult.has_badge && (
                      <span style={{ 
                        fontSize: '0.75rem', 
                        fontWeight: 'bold', 
                        background: 'rgba(6,182,212,0.15)', 
                        color: 'var(--primary)', 
                        padding: '2px 8px', 
                        borderRadius: '4px',
                        border: '1px solid rgba(6,182,212,0.3)',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px'
                      }}>
                        🔶 PRO
                      </span>
                    )}
                    {sellerVerifyResult.is_new_trader ? (
                      <span className="status-badge status-draft" style={{ fontSize: '0.75rem' }}>
                        New Trader
                      </span>
                    ) : (
                      <span className="status-badge status-completed" style={{ fontSize: '0.75rem' }}>
                        Active Merchant
                      </span>
                    )}
                  </div>

                  {/* Profile Body */}
                  {sellerVerifyResult.is_new_trader ? (
                    <div style={{ 
                      background: 'rgba(255,255,255,0.02)', 
                      border: '1px solid var(--border-muted)', 
                      padding: '16px', 
                      borderRadius: '8px', 
                      marginBottom: '16px' 
                    }}>
                      <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: 0 }}>
                        👤 <b>{sellerVerifyResult.phone_or_handle} · New Trader</b>
                      </p>
                      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', margin: '8px 0 0 0' }}>
                        This user is a New Trader with {sellerVerifyResult.completed_trades} completed trade(s). Full statistics and completion rates are hidden until they cross the minimum volume threshold.
                      </p>
                    </div>
                  ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '24px' }}>
                      <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>COMPLETED TRADES</span>
                        <p style={{ fontSize: '1.2rem', fontWeight: 'bold', margin: 0 }}>{sellerVerifyResult.completed_trades}</p>
                      </div>
                      <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>COMPLETION RATE</span>
                        <p style={{ fontSize: '1.2rem', fontWeight: 'bold', color: 'var(--primary)', margin: 0 }}>{sellerVerifyResult.completion_rate}%</p>
                      </div>
                      <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>POSITIVE RATINGS</span>
                        <p style={{ fontSize: '1.2rem', fontWeight: 'bold', color: 'var(--secondary)', margin: 0 }}>👍 {sellerVerifyResult.positive_rate}%</p>
                      </div>
                    </div>
                  )}

                  {/* Trust Score & Text display */}
                  <div style={{ background: 'var(--bg-panel)', padding: '16px', borderRadius: '8px', border: '1px solid var(--border-muted)', marginBottom: '16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                      <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Reputation Score:</span>
                      <span style={{ fontWeight: 'bold', color: sellerVerifyResult.trust_score >= 90 ? 'var(--primary)' : 'var(--accent-gold)' }}>
                        {sellerVerifyResult.trust_score}% Trust
                      </span>
                    </div>
                    <div style={{ borderTop: '1px dashed var(--border-muted)', paddingTop: '12px' }}>
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>BINANCE P2P FORMATTED DISPLAY:</span>
                      <pre style={{ 
                        margin: 0, 
                        background: '#070a0e', 
                        padding: '10px', 
                        borderRadius: '6px', 
                        fontSize: '0.8rem', 
                        color: 'white', 
                        fontFamily: 'monospace',
                        whiteSpace: 'pre-wrap'
                      }}>
                        {sellerVerifyResult.display_text}
                      </pre>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* CHAT SANDBOX SIMULATOR */}
        {activeTab === 'sandbox' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr 1fr', gap: '16px', minHeight: 'calc(100vh - 150px)' }}>
            
            {/* SELLER VIRTUAL MOBILE */}
            <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderBottom: '1px solid var(--border-muted)', paddingBottom: '12px', marginBottom: '12px' }}>
                <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: 'var(--primary)' }}></div>
                <h3 style={{ fontSize: '0.95rem' }}>Seller View (+254 711...)</h3>
              </div>
              
              {/* Chat View */}
              <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', background: '#0b141a', borderRadius: '8px', padding: '12px', minHeight: '260px' }}>
                {chatLogs.map((log, idx) => {
                  const isBot = log.sender_handle === 'Bot';
                  const isSeller = log.sender_handle === 'Seller';
                  return (
                    <div 
                      key={idx} 
                      className={`chat-bubble ${isBot ? 'chat-bot' : (isSeller ? 'chat-sender' : 'chat-receiver')}`}
                      style={{ fontSize: '0.8rem' }}
                    >
                      {log.is_revoked ? (
                        <span style={{ fontStyle: 'italic', color: 'var(--text-muted)' }}>This message was deleted.</span>
                      ) : (
                        <>
                          {log.message_content}
                          {log.media_url && (
                            <div style={{ marginTop: '4px', background: 'rgba(0,0,0,0.2)', padding: '4px', borderRadius: '4px', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
                              <Image size={12} /> {log.media_url.split('/').pop()}
                            </div>
                          )}
                          {!isBot && !log.is_revoked && (
                            <button 
                              onClick={() => handleRevokeMessage(log.id)} 
                              style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer', float: 'right', marginLeft: '8px' }}
                              title="Delete Message"
                            >
                              <Trash2 size={10} />
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
                <div ref={chatBottomRef} />
              </div>

              {/* Chat Input */}
              <form 
                onSubmit={(e) => { e.preventDefault(); sendSandboxMessage(sellerPhone, sellerMsg, 'Seller'); setSellerMsg(''); }}
                style={{ display: 'flex', gap: '4px', marginTop: '12px' }}
              >
                <input 
                  type="text" 
                  value={sellerMsg}
                  onChange={(e) => setSellerMsg(e.target.value)}
                  placeholder="Type 'SELL' or message..." 
                  className="form-input"
                  style={{ fontSize: '0.8rem' }}
                />
                <button type="submit" className="btn btn-primary" style={{ padding: '8px' }}>
                  <Send size={14} />
                </button>
              </form>

              {/* Quick Seller Actions */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '16px', borderTop: '1px solid var(--border-muted)', paddingTop: '12px' }}>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 'bold' }}>SELLER COMMAND SHORTCUTS</span>
                <button onClick={() => sendSandboxMessage(sellerPhone, 'SELL', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem' }}>
                  1. Setup Deal (SELL)
                </button>
                {dealDetails?.seller_session_state === 'AWAITING_DESC' && (
                  <div style={{ border: '1px solid var(--accent-gold)', padding: '8px', borderRadius: '6px', background: 'rgba(245,158,11,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-gold)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Preset Item Description:</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(sellerPhone, 'HP Pavilion Laptop, 8GB RAM, Used', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>💻 HP Pavilion Laptop, 8GB RAM, Used</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, 'Digital Artwork Logo Design', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>🎨 Digital Artwork Logo Design</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, 'Local Handoff Sofa Bed', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>🛋️ Local Handoff Sofa Bed</button>
                    </div>
                  </div>
                )}
                {dealDetails?.seller_session_state === 'AWAITING_PRICE' && (
                  <div style={{ border: '1px solid var(--accent-gold)', padding: '8px', borderRadius: '6px', background: 'rgba(245,158,11,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-gold)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Preset Price (KES):</span>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '15000', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>15,000</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '3500', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>3,500</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '8000', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>8,000</button>
                    </div>
                  </div>
                )}
                {dealDetails?.seller_session_state === 'AWAITING_TIMELINE' && (
                  <div style={{ border: '1px solid var(--accent-gold)', padding: '8px', borderRadius: '6px', background: 'rgba(245,158,11,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-gold)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Preset Timeline (Days):</span>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '3', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>3 Days</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '5', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>5 Days</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '7', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>7 Days</button>
                    </div>
                  </div>
                )}
                {dealDetails?.seller_session_state === 'AWAITING_DISPUTE_REASON' && (
                  <div style={{ border: '1px solid var(--accent-red)', padding: '8px', borderRadius: '6px', background: 'rgba(239,68,68,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-red)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Dispute Reason:</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(sellerPhone, 'Buyer claims non-delivery, but I shipped.', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>Buyer claims non-delivery, but I shipped</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, 'Buyer is not responding after delivery.', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>Buyer is not responding after delivery</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, 'Buyer damaged the item post-handoff.', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>Buyer damaged the item post-handoff</button>
                    </div>
                  </div>
                )}
                {dealDetails?.seller_session_state === 'AWAITING_RATING' && (
                  <div style={{ border: '1px solid var(--primary)', padding: '8px', borderRadius: '6px', background: 'rgba(59,130,246,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--primary)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Submit Manual Rating:</span>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '👍', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}>👍 Thumbs Up</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '👎', 'Seller')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>👎 Thumbs Down</button>
                    </div>
                  </div>
                )}
                {dealDetails?.deal?.status === 'awaiting_confirmation' && 
                 dealDetails.deal.transaction_type === 'shipped' && 
                 !dealDetails.deal.courier_name && (
                  <div style={{ border: '1px solid var(--accent-gold)', padding: '8px', borderRadius: '6px', background: 'rgba(245,158,11,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-gold)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Courier Service:</span>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '1', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>1. Sendy</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '2', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>2. G4S</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '3', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>3. Boxleo</button>
                      <button onClick={() => sendSandboxMessage(sellerPhone, '4', 'Seller')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem' }}>4. Kenya Post</button>
                    </div>
                  </div>
                )}
                {dealDetails?.deal?.status === 'awaiting_confirmation' && 
                 dealDetails.deal.transaction_type && 
                 (dealDetails.deal.transaction_type !== 'shipped' || dealDetails.deal.courier_name) && 
                 !dealDetails.deal.seller_disclaimer_acknowledged && (
                  <button 
                    onClick={() => sendSandboxMessage(sellerPhone, 'I ACKNOWLEDGE', 'Seller')} 
                    className="btn btn-secondary" 
                    style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-gold)', background: 'rgba(245,158,11,0.05)', marginTop: '4px' }}
                  >
                    📜 Acknowledge Disclaimer (I ACKNOWLEDGE)
                  </button>
                )}
                {dealDetails?.deal?.status === 'funded' && (
                  <div style={{ border: '1px solid rgba(255,255,255,0.05)', padding: '10px', borderRadius: '8px', marginTop: '8px', background: 'rgba(255,255,255,0.01)' }}>
                    <span style={{ fontSize: '0.75rem', color: 'var(--primary)', fontWeight: 'bold', display: 'block', marginBottom: '8px' }}>
                      Evidence Completion Panel (Type: {dealDetails.deal.transaction_type || 'shipped'})
                    </span>
                    
                    {/* TYPE 1: DIGITAL DELIVERABLE */}
                    {dealDetails.deal.transaction_type === 'digital' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <button onClick={() => handleSimulateUpload(null, { deliverable_file_url: '/simulated/files/holduntil_code_v1.zip', inAppCaptured: false })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}>
                          📁 Upload Deliverable File (.zip)
                        </button>
                        <button onClick={() => handleSimulateUpload('screenshot_proof.jpg', { inAppCaptured: true })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem' }}>
                          📷 Upload Screenshot Proof (In-App Camera)
                        </button>
                        <button onClick={() => handleSimulateUpload('screenshot_proof.jpg', { inAppCaptured: false })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>
                          📷 Upload Screenshot (Device Gallery - Rejects)
                        </button>
                        <button onClick={() => handleSimulateUpload('FAIL_CODE', { inAppCaptured: true })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-gold)' }}>
                          📷 Screenshot Proof (Wrong Code)
                        </button>
                      </div>
                    )}

                    {/* TYPE 2: SHIPPED GOODS (COURIER) */}
                    {(dealDetails.deal.transaction_type === 'shipped' || !dealDetails.deal.transaction_type) && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <div style={{ display: 'flex', gap: '4px', marginBottom: '4px' }}>
                          <input 
                            type="text" 
                            value={evidenceTrackingNumber} 
                            onChange={(e) => setEvidenceTrackingNumber(e.target.value)} 
                            placeholder="Tracking Number" 
                            className="form-input" 
                            style={{ fontSize: '0.75rem', padding: '4px', background: 'rgba(0,0,0,0.2)', color: 'white', border: '1px solid var(--border-muted)' }}
                          />
                          <button onClick={() => sendSandboxMessage(sellerPhone, `SHIPPED ${evidenceTrackingNumber}`, 'Seller')} className="btn btn-secondary" style={{ padding: '4px 8px', fontSize: '0.75rem' }}>
                            Update Tracking
                          </button>
                        </div>
                        <button onClick={() => handleSimulateUpload('package_with_code.jpg', { inAppCaptured: true })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}>
                          📷 Upload Package Photo (In-App Camera)
                        </button>
                        <button onClick={() => handleSimulateUpload('package_with_code.jpg', { inAppCaptured: false })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>
                          📷 Upload Package Photo (Device Gallery - Rejects)
                        </button>
                        <button onClick={() => handleSimulateUpload('FAIL_CODE', { inAppCaptured: true })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-gold)' }}>
                          📷 Package Photo (Wrong Code)
                        </button>
                        <button onClick={() => handleSimulateUpload('REUSE_ALERT', { inAppCaptured: true })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>
                          📷 Package Photo (Reused - Recycled Fraud)
                        </button>
                      </div>
                    )}

                    {/* TYPE 3: LOCAL IN-PERSON HANDOFF */}
                    {dealDetails.deal.transaction_type === 'handoff' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <button onClick={() => handleSimulateUpload('handover_proof.jpg', { inAppCaptured: true, gps_latitude: -1.2921, gps_longitude: 36.8219 })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}>
                          📷 Upload Handover Photo (In-App + GPS Location)
                        </button>
                        <button onClick={() => handleSimulateUpload('handover_proof.jpg', { inAppCaptured: true })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem' }}>
                          📷 Upload Handover Photo (In-App Only)
                        </button>
                        <button onClick={() => handleSimulateUpload('handover_proof.jpg', { inAppCaptured: false })} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>
                          📷 Handover Photo (Device Gallery - Rejects)
                        </button>
                      </div>
                    )}

                    {/* TYPE 4: REMOTE PHYSICAL SERVICE */}
                    {dealDetails.deal.transaction_type === 'remote_service' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '4px' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '4px', margin: '4px 0' }}>
                            <input 
                              type="checkbox" 
                              checked={videoCallBuyerPresent} 
                              onChange={(e) => setVideoCallBuyerPresent(e.target.checked)}
                            /> Buyer Attending in Real-Time
                          </label>
                          {!videoCallBuyerPresent && (
                            <input 
                              type="text" 
                              value={videoCallProxyName} 
                              onChange={(e) => setVideoCallProxyName(e.target.value)} 
                              placeholder="Nominated Proxy Name" 
                              className="form-input" 
                              style={{ fontSize: '0.75rem', padding: '4px', marginBottom: '4px', background: 'rgba(0,0,0,0.2)', color: 'white', border: '1px solid var(--border-muted)' }}
                            />
                          )}
                          <input 
                            type="text" 
                            value={videoCallMilestoneIndex} 
                            onChange={(e) => setVideoCallMilestoneIndex(e.target.value)} 
                            placeholder="Milestone Index (leave blank if final)" 
                            className="form-input" 
                            style={{ fontSize: '0.75rem', padding: '4px', marginBottom: '4px', background: 'rgba(0,0,0,0.2)', color: 'white', border: '1px solid var(--border-muted)' }}
                          />
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '4px' }}>
                            <span>Duration:</span>
                            <input 
                              type="number" 
                              value={videoCallDuration} 
                              onChange={(e) => setVideoCallDuration(parseInt(e.target.value) || 30)} 
                              style={{ width: '60px', padding: '2px', fontSize: '0.75rem', background: 'rgba(0,0,0,0.2)', color: 'white', border: '1px solid var(--border-muted)' }}
                            />s
                          </div>
                        </div>
                        <button 
                          onClick={() => handleSimulateVideoCall(videoCallDuration, videoCallBuyerPresent, videoCallProxyName || null, videoCallMilestoneIndex ? parseInt(videoCallMilestoneIndex) : null)} 
                          className="btn btn-secondary" 
                          style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}
                        >
                          📹 Initiate live in-app video call (Log proof)
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* SECURE LEDGER & STATE PANEL */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
              <div className="glass-card" style={{ padding: '20px', flex: 1 }}>
                <h3 style={{ fontSize: '1.1rem', marginBottom: '16px', borderBottom: '1px solid var(--border-muted)', paddingBottom: '8px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Lock size={18} color="var(--primary)" /> Escrow Secure Ledger
                </h3>

                {dealDetails ? (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
                      <span style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>Transaction Reference ID</span>
                      <code style={{ fontSize: '0.8rem', color: 'var(--secondary)' }}>{dealDetails.deal.id}</code>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '16px' }}>
                      <div style={{ background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '8px' }}>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Status</span>
                        <div>
                          <span className={`status-badge status-${dealDetails.deal.status}`}>
                            {dealDetails.deal.status}
                          </span>
                        </div>
                      </div>
                      <div style={{ background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '8px' }}>
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Locked Escrow Funds</span>
                        <p style={{ fontSize: '1.1rem', fontWeight: 'bold', color: 'var(--primary)' }}>
                          KES {dealDetails.deal.agreed_price?.toLocaleString()}.00
                        </p>
                      </div>
                    </div>

                    <div style={{ background: 'var(--bg-panel)', padding: '14px', borderRadius: '8px', border: '1px solid var(--border-muted)', marginBottom: '16px' }}>
                      <h4 style={{ fontSize: '0.8rem', marginBottom: '8px', color: 'var(--text-muted)' }}>ITEM DETAILS</h4>
                      <p style={{ fontSize: '0.9rem', marginBottom: '6px' }}>{dealDetails.deal.item_description}</p>
                      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', gap: '12px' }}>
                        <span>Code: <b>{dealDetails.deal.verification_code || 'Not Set'}</b></span>
                        {dealDetails.deal.tracking_number && (
                          <span>Tracking: <b>{dealDetails.deal.courier_name} {dealDetails.deal.tracking_number}</b></span>
                        )}
                      </div>
                    </div>

                    {/* Ledgers table */}
                    <div style={{ border: '1px solid var(--border-muted)', borderRadius: '8px', overflow: 'hidden' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem', textAlign: 'left' }}>
                        <thead style={{ background: 'rgba(255,255,255,0.03)' }}>
                          <tr>
                            <th style={{ padding: '8px' }}>Ref / Account</th>
                            <th style={{ padding: '8px' }}>Amount</th>
                            <th style={{ padding: '8px' }}>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {dealDetails.payments.length > 0 ? (
                            dealDetails.payments.map((p, idx) => (
                              <tr key={idx} style={{ borderTop: '1px solid var(--border-muted)' }}>
                                <td style={{ padding: '8px' }}>
                                  <span style={{ display: 'block', color: 'var(--text-muted)' }}>STK Push Ref:</span>
                                  <code>{p.stk_push_ref || 'None'}</code>
                                  {p.c2b_confirmation_ref && (
                                    <span style={{ display: 'block', color: 'var(--primary)' }}>Receipt: {p.c2b_confirmation_ref}</span>
                                  )}
                                  {p.b2c_payout_ref && (
                                    <div style={{ marginTop: '6px', borderTop: '1px dashed var(--border-muted)', paddingTop: '4px' }}>
                                      <span style={{ display: 'block', color: 'var(--text-muted)' }}>B2C Payout Ref:</span>
                                      <code style={{ fontSize: '0.7rem' }}>{p.b2c_payout_ref}</code>
                                      {(p.status === 'payout_processing' || p.status === 'refund_processing' || p.status === 'processing') && (
                                        <button
                                          onClick={async () => {
                                            const fd = new FormData();
                                            fd.append("payment_ref", p.b2c_payout_ref);
                                            const res = await fetch('/api/dashboard/simulation/mock-mpesa-b2c-callback', {
                                              method: 'POST',
                                              body: fd
                                            });
                                            if (res.ok) {
                                              alert("M-Pesa B2C callback simulated successfully!");
                                              fetchDealDetails(dealDetails.deal.id);
                                            } else {
                                              alert("Callback simulation failed.");
                                            }
                                          }}
                                          className="btn btn-primary"
                                          style={{ padding: '2px 6px', fontSize: '9px', marginTop: '4px', cursor: 'pointer', display: 'block' }}
                                        >
                                          Simulate B2C Webhook Callback
                                        </button>
                                      )}
                                    </div>
                                  )}
                                </td>
                                <td style={{ padding: '8px', fontWeight: 'bold' }}>KES {p.amount}</td>
                                <td style={{ padding: '8px' }}>
                                  <span className={`status-badge status-${p.status}`} style={{ fontSize: '9px' }}>{p.status}</span>
                                </td>
                              </tr>
                            ))
                          ) : (
                            <tr>
                              <td colSpan="3" style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)' }}>Awaiting payment initialization.</td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>

                  </div>
                ) : (
                  <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-muted)' }}>
                    <PlusCircle size={32} style={{ margin: '0 auto 12px auto' }} />
                    <p style={{ fontSize: '0.85rem', marginBottom: '4px' }}>No active transaction context loaded.</p>
                    <p style={{ fontSize: '0.75rem', marginBottom: '16px' }}>Type 'SELL' in the Seller view to initiate a deal flow.</p>
                    <div style={{ display: 'flex', gap: '8px', maxWidth: '320px', margin: '0 auto' }}>
                      <input 
                        type="text" 
                        placeholder="Paste invite link or Deal ID" 
                        className="form-input"
                        style={{ fontSize: '0.75rem', padding: '6px' }}
                        id="manual_deal_id_input"
                      />
                      <button 
                        onClick={() => {
                          const val = document.getElementById('manual_deal_id_input')?.value?.trim();
                          if (val) {
                            const uuidMatch = val.match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
                            const id = uuidMatch ? uuidMatch[0] : val;
                            setActiveDealId(id);
                            fetchDealDetails(id);
                          }
                        }}
                        className="btn btn-primary"
                        style={{ fontSize: '0.75rem', padding: '6px 12px' }}
                      >
                        Load
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* BUYER VIRTUAL MOBILE */}
            <div className="glass-card" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', borderBottom: '1px solid var(--border-muted)', paddingBottom: '12px', marginBottom: '12px' }}>
                <div style={{ width: '10px', height: '10px', borderRadius: '50%', background: 'var(--secondary)' }}></div>
                <h3 style={{ fontSize: '0.95rem' }}>Buyer View (+254 722...)</h3>
              </div>

              {/* Chat View */}
              <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', background: '#0b141a', borderRadius: '8px', padding: '12px', minHeight: '260px' }}>
                {chatLogs.map((log, idx) => {
                  const isBot = log.sender_handle === 'Bot';
                  const isBuyer = log.sender_handle === 'Buyer';
                  return (
                    <div 
                      key={idx} 
                      className={`chat-bubble ${isBot ? 'chat-bot' : (isBuyer ? 'chat-sender' : 'chat-receiver')}`}
                      style={{ fontSize: '0.8rem' }}
                    >
                      {log.is_revoked ? (
                        <span style={{ fontStyle: 'italic', color: 'var(--text-muted)' }}>This message was deleted.</span>
                      ) : (
                        <>
                          {log.message_content}
                          {log.media_url && (
                            <div style={{ marginTop: '4px', background: 'rgba(0,0,0,0.2)', padding: '4px', borderRadius: '4px', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
                              <Image size={12} /> {log.media_url.split('/').pop()}
                            </div>
                          )}
                          {!isBot && !log.is_revoked && (
                            <button 
                              onClick={() => handleRevokeMessage(log.id)} 
                              style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.4)', cursor: 'pointer', float: 'right', marginLeft: '8px' }}
                              title="Delete Message"
                            >
                              <Trash2 size={10} />
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Chat Input */}
              <form 
                onSubmit={(e) => { e.preventDefault(); sendSandboxMessage(buyerPhone, buyerMsg, 'Buyer'); setBuyerMsg(''); }}
                style={{ display: 'flex', gap: '4px', marginTop: '12px' }}
              >
                <input 
                  type="text" 
                  value={buyerMsg}
                  onChange={(e) => setBuyerMsg(e.target.value)}
                  placeholder="Paste JOIN code or message..." 
                  className="form-input"
                  style={{ fontSize: '0.8rem' }}
                />
                <button type="submit" className="btn btn-primary" style={{ padding: '8px' }}>
                  <Send size={14} />
                </button>
              </form>

              {/* Quick Buyer Actions */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '16px', borderTop: '1px solid var(--border-muted)', paddingTop: '12px' }}>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 'bold' }}>BUYER COMMAND SHORTCUTS</span>
                
                {dealDetails?.buyer_session_state === 'AWAITING_DISPUTE_REASON' && (
                  <div style={{ border: '1px solid var(--accent-red)', padding: '8px', borderRadius: '6px', background: 'rgba(239,68,68,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--accent-red)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Dispute Reason:</span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(buyerPhone, 'Item arrived damaged or broken.', 'Buyer')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>Item arrived damaged or broken</button>
                      <button onClick={() => sendSandboxMessage(buyerPhone, 'Seller failed to ship or respond.', 'Buyer')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>Seller failed to ship or respond</button>
                      <button onClick={() => sendSandboxMessage(buyerPhone, 'Service did not match description.', 'Buyer')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.7rem', textAlign: 'left' }}>Service did not match description</button>
                    </div>
                  </div>
                )}

                {dealDetails?.buyer_session_state === 'AWAITING_RATING' && (
                  <div style={{ border: '1px solid var(--primary)', padding: '8px', borderRadius: '6px', background: 'rgba(59,130,246,0.02)', marginTop: '4px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--primary)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Submit Manual Rating:</span>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
                      <button onClick={() => sendSandboxMessage(buyerPhone, '👍', 'Buyer')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}>👍 Thumbs Up</button>
                      <button onClick={() => sendSandboxMessage(buyerPhone, '👎', 'Buyer')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>👎 Thumbs Down</button>
                    </div>
                  </div>
                )}
                
                {dealDetails?.deal?.status === 'draft' && (
                  <button 
                    onClick={() => sendSandboxMessage(buyerPhone, `JOIN_${dealDetails.deal.id}`, 'Buyer')} 
                    className="btn btn-secondary" 
                    style={{ padding: '6px', fontSize: '0.75rem' }}
                  >
                    1. Tap Invite Link (JOIN)
                  </button>
                )}

                {dealDetails?.deal?.status === 'awaiting_confirmation' && (
                  <>
                    {/* Step A: Confirm summary */}
                    {!dealDetails.deal.buyer_confirmed && (
                      <button 
                        onClick={() => sendSandboxMessage(buyerPhone, 'CONFIRM', 'Buyer')} 
                        className="btn btn-secondary" 
                        style={{ padding: '6px', fontSize: '0.75rem' }}
                      >
                        2. Confirm Deal Summary
                      </button>
                    )}

                    {/* Step B: Select Transaction Type */}
                    {dealDetails.deal.buyer_confirmed && !dealDetails.deal.transaction_type && (
                      <div style={{ border: '1px solid var(--primary)', padding: '8px', borderRadius: '6px', background: 'rgba(16,185,129,0.02)', marginTop: '4px' }}>
                        <span style={{ fontSize: '0.7rem', color: 'var(--primary)', display: 'block', marginBottom: '6px', fontWeight: 'bold' }}>Select Transaction Type:</span>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                          <button onClick={() => sendSandboxMessage(buyerPhone, '1', 'Buyer')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem', textAlign: 'left' }}>1. Digital Deliverable</button>
                          <button onClick={() => sendSandboxMessage(buyerPhone, '2', 'Buyer')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem', textAlign: 'left' }}>2. Shipped Goods (Courier)</button>
                          <button onClick={() => sendSandboxMessage(buyerPhone, '3', 'Buyer')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem', textAlign: 'left' }}>3. Local In-Person Handoff</button>
                          <button onClick={() => sendSandboxMessage(buyerPhone, '4', 'Buyer')} className="btn btn-secondary" style={{ padding: '4px', fontSize: '0.7rem', textAlign: 'left' }}>4. Remote Physical Service</button>
                        </div>
                      </div>
                    )}

                    {/* Step C: Acknowledge Disclaimer */}
                    {dealDetails.deal.transaction_type && 
                     (dealDetails.deal.transaction_type !== 'shipped' || dealDetails.deal.courier_name) && 
                     !dealDetails.deal.buyer_disclaimer_acknowledged && (
                      <button 
                        onClick={() => sendSandboxMessage(buyerPhone, 'I ACKNOWLEDGE', 'Buyer')} 
                        className="btn btn-secondary" 
                        style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-gold)', background: 'rgba(245,158,11,0.05)', marginTop: '4px' }}
                      >
                        📜 Acknowledge Disclaimer (I ACKNOWLEDGE)
                      </button>
                    )}

                    {/* Step D: Payment */}
                    {dealDetails.deal.seller_disclaimer_acknowledged && dealDetails.deal.buyer_disclaimer_acknowledged && (
                      <button 
                        onClick={handleSimulatePayment} 
                        className="btn btn-secondary" 
                        style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)', background: 'rgba(16,185,129,0.05)', marginTop: '4px' }}
                      >
                        💳 Simulate M-Pesa Payment (STK Call)
                      </button>
                    )}
                  </>
                )}

                {dealDetails?.deal?.status === 'shipped' && !showBuyerConfirmDeclaration && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '4px' }}>
                    <button onClick={() => setShowBuyerConfirmDeclaration(true)} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--primary)' }}>
                      Confirm Receive (YES)
                    </button>
                    <button onClick={() => sendSandboxMessage(buyerPhone, 'NO', 'Buyer')} className="btn btn-secondary" style={{ padding: '6px', fontSize: '0.75rem', borderColor: 'var(--accent-red)' }}>
                      Dispute Deal (NO)
                    </button>
                  </div>
                )}

                {dealDetails?.deal?.status === 'shipped' && showBuyerConfirmDeclaration && (
                  <div style={{ padding: '8px', background: 'rgba(16,185,129,0.03)', border: '1px solid var(--border-muted)', borderRadius: '6px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                    <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontWeight: 'bold', textAlign: 'center' }}>⚠️ DECLARATION REQUIRED</span>
                    <button 
                      onClick={() => { sendSandboxMessage(buyerPhone, 'YES', 'Buyer'); setShowBuyerConfirmDeclaration(false); }} 
                      className="btn btn-primary" 
                      style={{ padding: '6px', fontSize: '0.7rem', background: 'var(--primary)', color: 'white', border: 'none', cursor: 'pointer' }}
                    >
                      1. I have confirmed and received
                    </button>
                    <button 
                      onClick={() => { sendSandboxMessage(buyerPhone, 'NO', 'Buyer'); setShowBuyerConfirmDeclaration(false); }} 
                      className="btn btn-secondary" 
                      style={{ padding: '6px', fontSize: '0.7rem', borderColor: 'var(--accent-red)', color: 'var(--accent-red)', cursor: 'pointer' }}
                    >
                      2. I confirm I haven't received the item
                    </button>
                    <button 
                      onClick={() => setShowBuyerConfirmDeclaration(false)} 
                      className="btn btn-secondary" 
                      style={{ padding: '4px', fontSize: '0.65rem', cursor: 'pointer' }}
                    >
                      Cancel
                    </button>
                  </div>
                )}
              </div>
            </div>

          </div>
        )}

        {/* HUMAN MODERATOR DASHBOARD */}
        {activeTab === 'moderator' && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '20px', minHeight: 'calc(100vh - 150px)' }}>
            
            {/* ESCALATIONS LIST */}
            <div className="glass-card" style={{ padding: '16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid var(--border-muted)', paddingBottom: '8px' }}>
                <h3 style={{ fontSize: '1.1rem', margin: 0 }}>Escalation Queue</h3>
                <div style={{ fontSize: '0.75rem', background: 'rgba(255,255,255,0.05)', padding: '4px 8px', borderRadius: '4px' }}>
                  AI Agreement: <b>{metrics.ai_agreement_rate_pct}%</b> ({metrics.total_disputes})
                </div>
              </div>
              
              <div style={{ overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {dashboardLoading ? (
                  <p>Loading queue...</p>
                ) : disputes.length > 0 ? (
                  disputes.map((disp, idx) => (
                    <div 
                      key={idx} 
                      onClick={() => selectDispute(disp.id)}
                      style={{ 
                        padding: '12px', 
                        borderRadius: '8px', 
                        background: selectedDisputeId === disp.id ? 'rgba(16,185,129,0.08)' : 'var(--bg-panel)',
                        border: selectedDisputeId === disp.id ? '1px solid var(--primary)' : '1px solid var(--border-muted)',
                        cursor: 'pointer'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '6px' }}>
                        <span style={{ fontWeight: '600', fontSize: '0.85rem' }}>{disp.item_description}</span>
                        <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                          {disp.sla_status === "breached" && (
                            <span style={{ fontSize: '8px', background: '#EF4444', color: 'white', padding: '2px 4px', borderRadius: '4px', fontWeight: 'bold' }}>🚨 SLA BREACH</span>
                          )}
                          {disp.sla_status === "approaching" && (
                            <span style={{ fontSize: '8px', background: '#F59E0B', color: 'black', padding: '2px 4px', borderRadius: '4px', fontWeight: 'bold' }}>⚠️ SLA WARN</span>
                          )}
                          {disp.is_appeal && (
                            <span style={{ fontSize: '8px', background: '#8B5CF6', color: 'white', padding: '2px 4px', borderRadius: '4px', fontWeight: 'bold' }}>🛡️ APPEAL</span>
                          )}
                          <span className={`status-badge ${disp.resolved_at ? 'status-completed' : 'status-disputed'}`} style={{ fontSize: '9px' }}>
                            {disp.resolved_at ? 'Resolved' : 'Active'}
                          </span>
                        </div>
                      </div>
                      <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: '8px' }}>
                        Filer: {disp.filed_by_handle} | Price: KES {disp.agreed_price}
                      </p>
                      <div style={{ fontSize: '0.75rem', background: 'rgba(0,0,0,0.1)', padding: '6px', borderRadius: '4px' }}>
                        Reason: {disp.reason.substring(0, 50)}...
                      </div>
                    </div>
                  ))
                ) : (
                  <div style={{ padding: '32px', textAlign: 'center', color: 'var(--text-muted)' }}>
                    <CheckCircle2 size={24} style={{ margin: '0 auto 8px auto', color: 'var(--primary)' }} />
                    <p style={{ fontSize: '0.85rem' }}>All escalations resolved. Clean queue!</p>
                  </div>
                )}
              </div>
            </div>

            {/* AUDIT TIMELINE & DECISION CENTER */}
            <div className="glass-card" style={{ padding: '20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
              {selectedDisputeDetails ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', height: '100%', overflowY: 'auto' }}>
                  
                  {/* Case Header */}
                  <div style={{ borderBottom: '1px solid var(--border-muted)', paddingBottom: '12px' }}>
                    <h3 style={{ fontSize: '1.25rem', marginBottom: '4px' }}>Dispute Detail: {selectedDisputeDetails.deal.item_description}</h3>
                    <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      Seller: <b>{selectedDisputeDetails.deal.seller_handle}</b> | Buyer: <b>{selectedDisputeDetails.deal.buyer_handle}</b> | Price: <b>KES {selectedDisputeDetails.deal.agreed_price}</b>
                    </p>
                  </div>

                  <div style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: '20px' }}>
                    
                    {/* LEFT PANEL: Chat history and Evidence */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      
                      {/* Transcript */}
                      <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                        <h4 style={{ fontSize: '0.85rem', color: 'var(--primary)', marginBottom: '8px', textTransform: 'uppercase' }}>Transaction Chat History</h4>
                        <div style={{ maxHeight: '200px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '0.75rem' }}>
                          {selectedDisputeDetails.chat_logs.map((log, idx) => (
                            <div key={idx} style={{ padding: '4px 6px', background: log.sender_handle === 'Bot' ? 'rgba(6,182,212,0.05)' : 'rgba(255,255,255,0.02)', borderRadius: '4px' }}>
                              <b style={{ color: log.sender_handle === 'Seller' ? 'var(--primary)' : (log.sender_handle === 'Buyer' ? 'var(--secondary)' : 'gray') }}>
                                {log.sender_handle}:
                              </b>{' '}
                              {log.is_revoked ? (
                                <span style={{ fontStyle: 'italic', color: 'var(--text-muted)' }}>[Message deleted by user - permanent record retained]</span>
                              ) : (
                                log.message_content
                              )}
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Evidence check results */}
                      <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                        <h4 style={{ fontSize: '0.85rem', color: 'var(--primary)', marginBottom: '8px', textTransform: 'uppercase' }}>Evidence Validation Checks</h4>
                        {selectedDisputeDetails.evidences.length > 0 ? (
                          selectedDisputeDetails.evidences.map((ev, idx) => (
                            <div key={idx} style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '0.75rem', borderTop: idx > 0 ? '1px solid var(--border-muted)' : 'none', paddingTop: idx > 0 ? '8px' : '0' }}>
                              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                                <span>Uploaded proof filename: <b>{ev.file_url.split('/').pop()}</b></span>
                                <span className={`status-badge ${ev.dynamic_code_detected ? 'status-completed' : 'status-disputed'}`}>
                                  {ev.dynamic_code_detected ? 'Code Matched' : 'Code Missing'}
                                </span>
                              </div>
                              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', margin: '4px 0' }}>
                                <div style={{ background: 'rgba(0,0,0,0.1)', padding: '6px', borderRadius: '4px' }}>
                                  <span>Courier Validation:</span>
                                  <b style={{ display: 'block', color: 'var(--secondary)' }}>{selectedDisputeDetails.deal.courier_name} API says {selectedDisputeDetails.deal.tracking_number ? 'Delivered' : 'Not Shipped'}</b>
                                </div>
                                <div style={{ background: 'rgba(0,0,0,0.1)', padding: '6px', borderRadius: '4px' }}>
                                  <span>Photo Reuse check:</span>
                                  {ev.perceptual_hash && ev.perceptual_hash.includes('reused') ? (
                                    <b style={{ display: 'block', color: 'var(--accent-red)' }}><ShieldAlert size={10} /> REUSE FRAUD ALERT</b>
                                  ) : (
                                    <b style={{ display: 'block', color: 'var(--primary)' }}>Unique (OK)</b>
                                  )}
                                </div>
                              </div>
                              {ev.exif_data && (
                                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                                  EXIF: Apple iPhone | Taken: {ev.exif_data.DateTime || 'N/A'} | GPS: Nairobi Local
                                </div>
                              )}
                            </div>
                          ))
                        ) : (
                          <p style={{ fontSize: '0.75rem', color: 'var(--accent-red)' }}>No shipping photo or courier evidence uploaded by seller.</p>
                        )}
                      </div>

                    </div>

                    {/* RIGHT PANEL: AI Recommendation & Resolution */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                      
                      {/* AI Tier 2 report */}
                      <div style={{ background: 'rgba(6,182,212,0.06)', border: '1px solid var(--secondary)', padding: '16px', borderRadius: '8px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                          <h4 style={{ fontSize: '0.85rem', color: 'var(--secondary)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <ShieldAlert size={14} /> Tier 2 AI Verdict
                          </h4>
                          <span style={{ fontSize: '0.8rem', fontWeight: 'bold', color: 'var(--secondary)' }}>
                            Confidence: {Math.round(selectedDisputeDetails.disputes[0].ai_confidence * 100)}%
                          </span>
                        </div>
                        <div style={{ fontSize: '0.8rem', marginBottom: '8px' }}>
                          Proposed Outcome:{' '}
                          <span className={`status-badge status-${selectedDisputeDetails.disputes[0].ai_decision}`}>
                            {selectedDisputeDetails.disputes[0].ai_decision}
                          </span>
                        </div>
                        <p style={{ fontSize: '0.75rem', lineHeight: '1.4', background: 'rgba(0,0,0,0.2)', padding: '8px', borderRadius: '4px', fontStyle: 'italic' }}>
                          "{selectedDisputeDetails.disputes[0].ai_reasoning}"
                        </p>
                      </div>

                      {selectedDisputeDetails.disputes[0].appeal_fee_payout_ref && (
                        <div style={{ background: 'rgba(139,92,246,0.06)', border: '1px solid var(--primary)', padding: '12px', borderRadius: '8px', fontSize: '0.75rem', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                          <div style={{ fontWeight: 'bold' }}>Appeal Refund Status</div>
                          <div>Status: <span className="status-badge" style={{ fontSize: '9px' }}>{selectedDisputeDetails.disputes[0].appeal_fee_refund_status}</span></div>
                          {selectedDisputeDetails.disputes[0].appeal_fee_refund_status === 'pending' && (
                            <button
                              type="button"
                              onClick={async () => {
                                const fd = new FormData();
                                fd.append("payment_ref", selectedDisputeDetails.disputes[0].appeal_fee_payout_ref);
                                const res = await fetch('/api/dashboard/simulation/mock-mpesa-b2c-callback', {
                                  method: 'POST',
                                  body: fd
                                });
                                if (res.ok) {
                                  alert("Appeal refund webhook callback simulated successfully!");
                                  // Refresh data
                                  const discRes = await fetch('/api/dashboard/disputes');
                                  if (discRes.ok) {
                                    const data = await discRes.json();
                                    setDisputes(data);
                                    selectDispute(selectedDisputeId);
                                  }
                                } else {
                                  alert("Simulation failed.");
                                }
                              }}
                              className="btn btn-primary"
                              style={{ padding: '4px 8px', fontSize: '9px', cursor: 'pointer', alignSelf: 'flex-start' }}
                            >
                              Simulate Appeal Refund Webhook Callback
                            </button>
                          )}
                        </div>
                      )}

                      {/* Checklist */}
                      <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)', fontSize: '0.75rem' }}>
                        <h4 style={{ fontSize: '0.85rem', marginBottom: '8px' }}>Human Checklist Rubric</h4>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <input type="checkbox" checked={checklist.codeMatch} onChange={(e) => setChecklist(prev => ({ ...prev, codeMatch: e.target.checked }))} />
                            Code "{selectedDisputeDetails.deal.verification_code}" visible in package photo
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <input type="checkbox" checked={checklist.timelineValid} onChange={(e) => setChecklist(prev => ({ ...prev, timelineValid: e.target.checked }))} />
                            Shipment timeframe fits within deadline
                          </label>
                          <label style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <input type="checkbox" checked={checklist.descMatch} onChange={(e) => setChecklist(prev => ({ ...prev, descMatch: e.target.checked }))} />
                            Item matches listing description
                          </label>
                        </div>
                      </div>

                      {/* Decision Center Form */}
                      <form onSubmit={handleResolveDispute} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                        <div>
                          <label style={{ fontSize: '0.75rem', display: 'block', marginBottom: '4px' }}>Assign Staff User (Resolver Role Check)</label>
                          <select 
                            value={resolverId} 
                            onChange={(e) => setResolverId(e.target.value)}
                            className="form-input"
                          >
                            <option value="MOD_1">MOD_1 (Mediator role - first-instance only)</option>
                            <option value="ARB_1">ARB_1 (Arbitrator role - can resolve appeals)</option>
                            <option value="ADMIN_1">ADMIN_1 (Admin role - super permissions)</option>
                          </select>
                        </div>

                        <div>
                          <label style={{ fontSize: '0.75rem', display: 'block', marginBottom: '4px' }}>Verdict Outcome</label>
                          <select 
                            value={modOutcome} 
                            onChange={(e) => setModOutcome(e.target.value)}
                            className="form-input"
                          >
                            <option value="release">Release Payout to Seller (100%)</option>
                            <option value="refund">Refund Buyer (100%)</option>
                            <option value="partial_split">Split Escrow Funds</option>
                          </select>
                        </div>

                        {modOutcome === 'partial_split' && (
                          <div>
                            <label style={{ fontSize: '0.75rem', display: 'block', marginBottom: '4px' }}>Seller Percentage: {modSplitPct}%</label>
                            <input 
                              type="range" 
                              min="1" 
                              max="99" 
                              value={modSplitPct} 
                              onChange={(e) => setModSplitPct(e.target.value)} 
                              style={{ width: '100%' }}
                            />
                          </div>
                        )}

                        <div>
                          <label style={{ fontSize: '0.75rem', display: 'block', marginBottom: '4px' }}>Audit Resolution Statement</label>
                          <textarea 
                            value={moderatorReasoning}
                            onChange={(e) => setModeratorReasoning(e.target.value)}
                            placeholder="State the evidence which drove this manual outcome (e.g. tracking verified or reuse photo alert)..."
                            className="form-input"
                            style={{ minHeight: '60px', resize: 'vertical' }}
                          />
                        </div>

                        <button type="submit" className="btn btn-primary" style={{ width: '100%' }}>
                          Apply Human Resolution & Trigger Payout
                        </button>
                      </form>

                    </div>

                  </div>

                </div>
              ) : (
                <div style={{ textAlign: 'center', padding: '100px 0', color: 'var(--text-muted)' }}>
                  <Gavel size={36} style={{ margin: '0 auto 12px auto' }} />
                  <p>Select a disputed transaction from the queue to start Human Moderation review.</p>
                </div>
              )}
            </div>

          </div>
        )}

        {/* ADMIN PANEL */}
        {activeTab === 'admin' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
            
            {/* Sandbox Debugging Tools */}
            <div className="glass-card" style={{ padding: '20px', border: '1px solid var(--accent-red)' }}>
              <h3 style={{ fontSize: '1.1rem', marginBottom: '8px', color: 'var(--accent-red)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <ShieldAlert size={18} /> Sandbox Debugging Tools
              </h3>
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginBottom: '16px' }}>
                If you get stuck or want to clear previous transactions to test different scenarios from scratch, use the button below to purge all records.
              </p>
              <button 
                onClick={handleResetAll} 
                className="btn btn-primary" 
                style={{ background: 'var(--accent-red)', borderColor: 'var(--accent-red)', color: 'white', padding: '10px 16px', fontSize: '0.85rem', cursor: 'pointer' }}
              >
                🚨 Purge Database & Reset Sessions
              </button>
            </div>

            {/* Config details & Editor */}
            <div className="glass-card" style={{ padding: '20px' }}>
              <h3 style={{ fontSize: '1.1rem', marginBottom: '16px' }}>Escrow Configuration & Settings</h3>
              <form onSubmit={handleUpdateSettings} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '16px' }}>
                  <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                    <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>Min Trades for Profile Stats (Threshold)</label>
                    <input 
                      type="number" 
                      min="0"
                      value={systemSettings.MIN_TRADES_FOR_PROFILE_STATS} 
                      onChange={(e) => setSystemSettings(prev => ({ ...prev, MIN_TRADES_FOR_PROFILE_STATS: parseInt(e.target.value) || 0 }))}
                      className="form-input"
                      style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-muted)', color: 'white', fontSize: '1rem', fontWeight: 'bold', width: '100%', padding: '8px' }}
                    />
                    <p style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '4px' }}>Under this cutoff, traders are labeled "New Trader" without public stats.</p>
                  </div>
                  <div style={{ background: 'var(--bg-panel)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-muted)' }}>
                    <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'block', marginBottom: '6px' }}>Min Deals for [PRO] Badge</label>
                    <input 
                      type="number" 
                      min="0"
                      value={systemSettings.MIN_DEALS_FOR_BADGE} 
                      onChange={(e) => setSystemSettings(prev => ({ ...prev, MIN_DEALS_FOR_BADGE: parseInt(e.target.value) || 0 }))}
                      className="form-input"
                      style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-muted)', color: 'white', fontSize: '1rem', fontWeight: 'bold', width: '100%', padding: '8px' }}
                    />
                    <p style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '4px' }}>Deals needed to unlock the [PRO] Verified Safe Seller badge.</p>
                  </div>
                </div>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', alignItems: 'center' }}>
                  {settingsLoading && <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Saving settings...</span>}
                  <button type="submit" className="btn btn-primary" style={{ padding: '8px 16px', fontSize: '0.85rem' }}>
                    Save Escrow Configuration
                  </button>
                </div>
              </form>
            </div>

            {/* Users trust registry list */}
            <div className="glass-card" style={{ padding: '20px' }}>
              <h3 style={{ fontSize: '1.1rem', marginBottom: '12px' }}>System Trust Registry</h3>
              
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', textAlign: 'left' }}>
                  <thead style={{ background: 'rgba(255,255,255,0.03)', borderBottom: '1px solid var(--border-muted)' }}>
                    <tr>
                      <th style={{ padding: '12px' }}>User ID</th>
                      <th style={{ padding: '12px' }}>Phone / Handle</th>
                      <th style={{ padding: '12px' }}>Role</th>
                      <th style={{ padding: '12px' }}>Trust Score</th>
                      <th style={{ padding: '12px' }}>Disputes Overturned</th>
                      <th style={{ padding: '12px' }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usersLoading ? (
                      <tr>
                        <td colSpan="6" style={{ padding: '16px', textAlign: 'center' }}>Loading user registry...</td>
                      </tr>
                    ) : users.length > 0 ? (
                      users.map((u, idx) => (
                        <tr key={idx} style={{ borderBottom: '1px solid var(--border-muted)' }}>
                          <td style={{ padding: '12px' }}><code>{u.id}</code></td>
                          <td style={{ padding: '12px', fontWeight: '600' }}>{u.phone_or_handle}</td>
                          <td style={{ padding: '12px' }}>
                            <span style={{ fontSize: '10px', background: 'rgba(255,255,255,0.05)', padding: '2px 6px', borderRadius: '4px' }}>{u.role}</span>
                          </td>
                          <td style={{ padding: '12px' }}>
                            <span style={{ fontWeight: 'bold', color: u.trust_score >= 90 ? 'var(--primary)' : 'var(--accent-gold)' }}>
                              {u.trust_score}%
                            </span>
                          </td>
                          <td style={{ padding: '12px' }}>{u.ai_overturn_flag_count || 0} times</td>
                          <td style={{ padding: '12px', display: 'flex', gap: '8px' }}>
                            <button 
                              onClick={() => {
                                const newScore = prompt("Enter new trust score (0 - 100):", u.trust_score);
                                if (newScore !== null) handleOverrideTrust(u.id, newScore);
                              }}
                              className="btn btn-secondary" 
                              style={{ padding: '4px 8px', fontSize: '0.75rem' }}
                            >
                              Edit Trust
                            </button>
                          </td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan="6" style={{ padding: '16px', textAlign: 'center', color: 'var(--text-muted)' }}>No records loaded. Play with the Sandbox simulator to register users automatically.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

          </div>
        )}

      </main>

      {/* FOOTER */}
      <footer style={{ textAlign: 'center', padding: '16px', color: 'var(--text-muted)', fontSize: '0.75rem', borderTop: '1px solid var(--border-muted)', margin: '16px 16px 0 16px' }}>
        <p>Disclaimer: HoldUntil is an independent social commerce escrow platform. We are not officially affiliated with Safaricom M-Pesa, Meta, WhatsApp, Messenger, or Instagram.</p>
        <p style={{ marginTop: '4px' }}>HoldUntil is compliant with the Kenya Data Protection Act, 2019. Dialogue recordings are strictly transaction-scoped.</p>
      </footer>
    </div>
  );
}
