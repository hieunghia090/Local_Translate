import { Navigate, Route, Routes } from 'react-router-dom';
import Topbar from './components/Topbar';
import CreateBookPage from './pages/CreateBookPage';
import ChapterPage from './pages/ChapterPage';
import LibraryPage from './pages/LibraryPage';
import WorkspacePage from './pages/WorkspacePage';

export default function App() {
  return (
    <div className="wrap">
      <Topbar />
      <Routes>
        <Route path="/" element={<LibraryPage />} />
        <Route path="/books/new" element={<CreateBookPage />} />
        <Route path="/books/:bookId/chapters/:no" element={<ChapterPage />} />
        <Route path="/books/:bookId/:tab?" element={<WorkspacePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}
