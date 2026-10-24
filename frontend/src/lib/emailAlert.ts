/**
 * Emergency contact email alerts via EmailJS
 * Free tier: 200 emails/month, sends to ANY email address
 * No domain verification needed
 *
 * Set these in your .env.local / Vercel environment variables:
 *   VITE_EMAILJS_SERVICE_ID  — from EmailJS dashboard → Email Services
 *   VITE_EMAILJS_TEMPLATE_ID — from EmailJS dashboard → Email Templates
 *   VITE_EMAILJS_PUBLIC_KEY  — from EmailJS dashboard → Account → Public Key
 */

import emailjs from "@emailjs/browser";

const SERVICE_ID  = import.meta.env.VITE_EMAILJS_SERVICE_ID  as string;
const TEMPLATE_ID = import.meta.env.VITE_EMAILJS_TEMPLATE_ID as string;
const PUBLIC_KEY  = import.meta.env.VITE_EMAILJS_PUBLIC_KEY  as string;

export interface AlertContact {
  name:  string;
  email: string;
}

export async function sendEmergencyAlerts(params: {
  contacts:     AlertContact[];
  incidentType: string;
  summary:      string;
  victimName:   string;
  location:     string;
}): Promise<{ sent: string[]; failed: string[] }> {
  if (!SERVICE_ID || !TEMPLATE_ID || !PUBLIC_KEY) {
    console.warn("[EmailJS] Missing env vars — skipping email alerts");
    return { sent: [], failed: [] };
  }

  emailjs.init(PUBLIC_KEY);

  const sent:   string[] = [];
  const failed: string[] = [];

  for (const contact of params.contacts) {
    try {
      await emailjs.send(SERVICE_ID, TEMPLATE_ID, {
        to_email:      contact.email,
        to_name:       contact.name,
        email:         contact.email,
        name:          contact.name,
        victim_name:   params.victimName  || "Unknown",
        incident_type: params.incidentType,
        summary:       params.summary,
        location:      params.location,
      });
      console.log(`[EmailJS] Sent to ${contact.email}`);
      sent.push(contact.email);
    } catch (e) {
      console.error(`[EmailJS] Failed to send to ${contact.email}:`, e);
      failed.push(contact.email);
    }
  }

  return { sent, failed };
}
