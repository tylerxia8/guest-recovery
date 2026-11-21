"use client";

import { useEffect, useState } from "react";
import { api } from "../lib/api";

type House = {id: string; name: string; latitude: number | null; longitude: number | null; address: string | null; unit: string | null; status: string};
type Cleaner = {id: string; name: string; starting_address: string; status: string};
type LocationData = {houses: House[]; cleaners: Cleaner[]};

export function Locations() {
  const [data, setData] = useState<LocationData | null>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    api<LocationData>("/locations").then(result => {if(active) {setData(result); setError("");}}).catch(() => {if(active) setError("Locations could not be loaded. Manager access is required.");});
    return () => {active = false;};
  }, [reload]);
  const query = search.trim().toLocaleLowerCase();
  const houses = data?.houses.filter(house => house.name.toLocaleLowerCase().includes(query)) ?? [];
  const cleaners = data?.cleaners.filter(cleaner => `${cleaner.name} ${cleaner.starting_address}`.toLocaleLowerCase().includes(query)) ?? [];
  return <main className="locations-page"><header className="locations-heading"><div><p className="eyebrow">TRAVEL</p><h1>Locations</h1></div><a href="/setup">Back to setup</a></header>
    <label className="locations-search">Find a location<input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="House, cleaner or starting address" /></label>
    {error ? <p role="alert">{error} <button onClick={() => setReload(value => value + 1)}>Retry</button></p> : null}
    {!data && !error ? <p>Loading locations…</p> : null}
    {data ? <><section><div className="locations-section-heading"><h2>Cleaner starts <span className="badge">{cleaners.length}/{data.cleaners.length}</span></h2><a href="/cleaners">Edit starting addresses</a></div>
      {cleaners.length ? <div className="locations-table"><table><thead><tr><th scope="col">Cleaner</th><th scope="col">Starting address</th><th scope="col">Routes</th></tr></thead><tbody>{cleaners.map(cleaner => <tr key={cleaner.id}><th scope="row">{cleaner.name}</th><td>{cleaner.starting_address || "Not set"}</td><td><span className={`badge${cleaner.status === "ready" ? "" : " attention"}`}>{cleaner.status === "ready" ? "Ready" : cleaner.status === "missing_address" ? "Add address" : "Needs review"}</span></td></tr>)}</tbody></table></div> : <p className="muted">{query ? "No matching cleaner starts." : "No active cleaners."}</p>}
    </section><section><div className="locations-section-heading"><h2>Houses <span className="badge">{houses.length}/{data.houses.length}</span></h2></div>
      {houses.length ? <div className="locations-table"><table><thead><tr><th scope="col">House</th><th scope="col">Location</th><th scope="col">Map status</th></tr></thead><tbody>{houses.map(house => <tr key={house.id}><th scope="row">{house.name}</th><td>{house.address ? <><strong>{house.address}</strong>{house.unit ? <small>Unit {house.unit}</small> : null}</> : null}{house.latitude !== null && house.longitude !== null ? <a href={`https://www.openstreetmap.org/?mlat=${house.latitude}&mlon=${house.longitude}#map=17/${house.latitude}/${house.longitude}`} target="_blank" rel="noreferrer">Open map <span className="locations-coordinate">{house.latitude.toFixed(5)}, {house.longitude.toFixed(5)}</span></a> : "Missing source location"}</td><td><span className={`badge${house.status === "mapped" ? "" : " attention"}`}>{house.status === "mapped" ? "Mapped" : "Needs review"}</span></td></tr>)}</tbody></table></div> : <p className="muted">{query ? "No matching houses." : "No active houses."}</p>}
    </section><p className="muted locations-note">Active locations only · Map data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a></p></> : null}
  </main>;
}
