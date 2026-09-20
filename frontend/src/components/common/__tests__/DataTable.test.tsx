import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DataTable, type DataTableColumn } from '../DataTable';

interface Row {
  id: number;
  name: string;
}

const columns: DataTableColumn<Row>[] = [
  { key: 'name', header: '名称', render: (row) => row.name },
];

const rowKey = (row: Row) => String(row.id);

describe('DataTable', () => {
  it('loading 时渲染加载态', () => {
    render(<DataTable columns={columns} rows={undefined} rowKey={rowKey} loading />);
    expect(screen.getByRole('status')).toHaveTextContent('加载中');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('error 时渲染错误态并支持重试', () => {
    const onRetry = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={undefined}
        rowKey={rowKey}
        error={new Error('接口 500')}
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('接口 500');
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('空数据时渲染空态', () => {
    render(<DataTable columns={columns} rows={[]} rowKey={rowKey} emptyTitle="暂无用户" />);
    expect(screen.getByText('暂无用户')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('渲染表头与数据行', () => {
    render(
      <DataTable
        columns={columns}
        rows={[
          { id: 1, name: '龙回头' },
          { id: 2, name: '首阴' },
        ]}
        rowKey={rowKey}
      />,
    );

    expect(screen.getByRole('columnheader', { name: '名称' })).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(3);
    expect(screen.getByText('龙回头')).toBeInTheDocument();
    expect(screen.getByText('首阴')).toBeInTheDocument();
  });

  it('分页条按 total/pageSize 计算并在边界禁用', () => {
    const onPageChange = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={[{ id: 1, name: '龙回头' }]}
        rowKey={rowKey}
        page={1}
        pageSize={10}
        total={25}
        onPageChange={onPageChange}
      />,
    );

    expect(screen.getByText(/第 1 \/ 3 页/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
