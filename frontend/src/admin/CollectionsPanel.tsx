import { useState } from 'react';
import type { AdminCollection } from '../api/types';
import {
  createAdminCollection,
  deleteAdminCollection,
  listAdminCollections,
  renameAdminCollection,
} from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
import {
  AdminError,
  AdminWorkspace,
  DetailHead,
  FieldLine,
  PAGE,
  Pager,
  RefreshButton,
  usePagedList,
  type AdminPanelProps,
} from './adminUi';

export function CollectionsPanel({ toolbar }: AdminPanelProps) {
  const list = usePagedList((offset) => listAdminCollections(PAGE, offset));
  const [selected, setSelected] = useState<AdminCollection | null>(null);
  const [newName, setNewName] = useState('');
  const [editName, setEditName] = useState('');

  const select = (collection: AdminCollection) => {
    setSelected(collection);
    setEditName(collection.name);
  };

  const create = async () => {
    if (!newName.trim()) return;
    await list.run(async () => {
      await createAdminCollection(newName.trim());
      setNewName('');
      await list.reload();
    });
  };

  const rename = async (collection: AdminCollection) => {
    if (!editName.trim()) return;
    await list.run(async () => {
      select(await renameAdminCollection(collection.collectionId, editName.trim()));
      await list.reload();
    });
  };

  const remove = async (collection: AdminCollection) => {
    if (!window.confirm(`Delete empty collection “${collection.name}”?`)) return;
    await list.run(async () => {
      await deleteAdminCollection(collection.collectionId);
      setSelected(null);
      await list.reload();
    });
  };

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={list.working} onRefresh={() => void list.reload()} />
      <AdminError error={list.error} />
      <form
        className="admin-inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          void create();
        }}
      >
        <input
          value={newName}
          onChange={(event) => setNewName(event.target.value)}
          placeholder="New collection name"
          aria-label="New collection name"
        />
        <button type="submit" className="primary-button" disabled={list.working || !newName.trim()}>
          Create collection
        </button>
      </form>
      <AdminWorkspace
        detail={
          selected && (
            <>
              <DetailHead title={selected.name} onClose={() => setSelected(null)} />
              <FieldLine label="Collection">
                <span className="mono">{selected.collectionId}</span>
              </FieldLine>
              <FieldLine label="Sources">{selected.sourceCount}</FieldLine>
              <FieldLine label="Created">{formatTimestamp(selected.createdAt)}</FieldLine>
              <label className="admin-field-line">
                <span>Name</span>
                <input value={editName} onChange={(event) => setEditName(event.target.value)} />
              </label>
              <div className="admin-actions">
                <button
                  type="button"
                  className="primary-button"
                  disabled={list.working || !editName.trim()}
                  onClick={() => void rename(selected)}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className="danger-button"
                  disabled={list.working || selected.sourceCount > 0}
                  onClick={() => void remove(selected)}
                  title={selected.sourceCount ? 'Only empty collections can be deleted' : undefined}
                >
                  Delete
                </button>
              </div>
            </>
          )
        }
      >
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Sources</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {list.rows.map((collection) => (
                <tr key={collection.collectionId} className="admin-clickable" onClick={() => select(collection)}>
                  <td>{collection.name}</td>
                  <td>{collection.sourceCount}</td>
                  <td>{formatTimestamp(collection.createdAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager offset={list.offset} count={list.rows.length} busy={list.working} onOffset={list.setOffset} />
      </AdminWorkspace>
    </>
  );
}
